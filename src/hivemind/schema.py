"""Declarative models for a HiveMind organisation.

Everything an agent fleet needs is described by these models and loaded from YAML:

* ``OrgConfig``      - ``hivemind.yaml``: org-wide defaults, goals, teams, policies, environments.
* ``ToolDef``        - ``catalog/tools/*.yaml``: a tool defined once, referenced by many agents.
* ``KnowledgeDef``   - ``catalog/knowledge/*.yaml``: a knowledge source (SharePoint, AI Search, Fabric...).
* ``RoleDef``        - ``roles/*.yaml``: a reusable archetype (worker, manager, reviewer, executive...).
* ``AgentSpec``      - ``agents/*.yaml``: one digital worker - its job, place in the org, tools, autonomy, evals.
* ``ProcessSpec``    - ``processes/*.yaml``: an end-to-end process run by agents (an Agent Framework workflow).

The models are deliberately provider-neutral; ``hivemind.compiler`` turns them into Foundry definitions and
``hivemind.azd`` into an ``azure.yaml`` that the Azure Developer CLI deploys.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,62}$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _check_name(value: str) -> str:
    if not NAME_PATTERN.match(value):
        raise ValueError(f"'{value}' must be lowercase kebab-case, start with a letter and be 2-63 characters")
    return value


# --------------------------------------------------------------------------------------------------
# Shared building blocks
# --------------------------------------------------------------------------------------------------


class Tier(StrEnum):
    """Where an agent sits in the organisation. Drives default permissions and policy checks."""

    EXECUTIVE = "executive"
    MANAGER = "manager"
    REVIEWER = "reviewer"
    WORKER = "worker"


class AutonomyLevel(StrEnum):
    """How much an agent may do on its own. Agents earn promotion with evidence (evals, sign-off).

    * ``shadow``             - runs alongside people; its output is not used and it takes no actions.
    * ``suggest``            - proposes what to do; a person does it. No write tools.
    * ``approve``            - acts, but every change needs a person's approval (MCP require_approval).
    * ``autonomous``         - acts on its own within its tools and policies.
    * ``autonomous_sampled`` - acts on its own; a share of its work is reviewed by people after the fact.
    """

    SHADOW = "shadow"
    SUGGEST = "suggest"
    APPROVE = "approve"
    AUTONOMOUS = "autonomous"
    AUTONOMOUS_SAMPLED = "autonomous_sampled"

    @property
    def rank(self) -> int:
        return list(AutonomyLevel).index(self)


class InstructionSource(_Model):
    """One fragment of an agent's instructions. Exactly one of the fields must be set.

    * ``file``       - path relative to the org directory (version-controlled with the spec).
    * ``sharepoint`` - a document in a SharePoint library, owned by the business (fetched via Graph).
    * ``url``        - any HTTPS URL returning text/markdown.
    * ``inline``     - literal text.
    """

    file: str | None = None
    sharepoint: SharePointRef | None = None
    url: str | None = None
    inline: str | None = None
    title: str | None = Field(default=None, description="Optional heading inserted above the fragment.")

    @model_validator(mode="after")
    def _exactly_one(self) -> InstructionSource:
        set_fields = [f for f in ("file", "sharepoint", "url", "inline") if getattr(self, f) is not None]
        if len(set_fields) != 1:
            raise ValueError(f"instruction source needs exactly one of file/sharepoint/url/inline, got {set_fields}")
        return self

    def describe(self) -> str:
        if self.file:
            return f"file:{self.file}"
        if self.sharepoint:
            return f"sharepoint:{self.sharepoint.site}/{self.sharepoint.path}"
        if self.url:
            return f"url:{self.url}"
        return "inline"


class SharePointRef(_Model):
    site: str = Field(description="Site URL without scheme, e.g. contoso.sharepoint.com/sites/Finance")
    path: str = Field(description="Path inside the site's default document library, e.g. 'Agents/ap-clerk.md'")
    drive: str | None = Field(default=None, description="Document library name when not the default one.")


InstructionSource.model_rebuild()


class Budget(_Model):
    monthly_usd: float | None = Field(default=None, ge=0)
    cost_center: str | None = None
    alert_at_percent: int = Field(default=80, ge=1, le=100)


class Schedule(_Model):
    """Run the agent unattended. Compiled to a Foundry routine with a schedule trigger."""

    name: str
    cron: str
    time_zone: str = "UTC"
    input: str
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _check_name(value)


class M365Channel(_Model):
    """Publish the agent to Microsoft 365 Copilot and Teams."""

    enabled: bool = False
    scope: Literal["personal", "shared", "tenant"] = "shared"
    display_name: str | None = None
    short_description: str | None = None


class Channels(_Model):
    m365: M365Channel = Field(default_factory=M365Channel)
    api: bool = True


class MemoryConfig(_Model):
    """Per-agent long-term memory: a Foundry memory store behind the native ``memory_search`` tool."""

    private: bool = True
    private_scope: Literal["agent", "user"] = Field(
        default="user",
        description="'user' partitions memory per end user (safe for Teams/M365 agents); "
        "'agent' gives one memory for the agent itself (suits unattended agents).",
    )


class Autonomy(_Model):
    """The agent's current autonomy level and the evidence for it. Changes go through pull requests.

    New agents start in ``shadow``, like a new hire shadowing the person who does the job today.
    """

    level: AutonomyLevel = AutonomyLevel.SHADOW
    sample_rate: float | None = Field(
        default=None, gt=0, le=1, description="autonomous_sampled: share of runs routed to human review."
    )
    approved_by: str | None = Field(default=None, description="Who signed off this level (UPN/email).")
    approved_on: str | None = Field(default=None, description="When (YYYY-MM-DD).")
    evidence: str | None = Field(default=None, description="Link to the eval run, pilot report or decision record.")


class EvalSpec(_Model):
    """Loop engineering for one agent: a golden dataset that gates deployment, plus production trace evaluation.

    Compiled to an ``azure.ai.eval`` service; ``hivemind gate`` runs it with a pass-rate threshold.
    """

    dataset: str | None = Field(default=None, description="JSONL of {query, expected?} rows, relative to the org.")
    evaluators: list[str] = Field(default_factory=list, description="Defaults to policies.evals.evaluators.")
    pass_rate: float | None = Field(default=None, ge=0, le=1, description="Overrides the autonomy level's gate.")
    trace_eval: bool = Field(default=True, description="Also evaluate a sample of real production traces.")


# --------------------------------------------------------------------------------------------------
# Catalog: tools and knowledge
# --------------------------------------------------------------------------------------------------

ToolKind = Literal[
    "mcp",
    "openapi",
    "azure_ai_search",
    "sharepoint",
    "fabric_data_agent",
    "fabric_iq",
    "work_iq",
    "code_interpreter",
    "web_search",
    "bing_grounding",
    "a2a",
    "raw",
]


class ToolDef(_Model):
    """A tool registered once in the catalog and granted to agents by id.

    ``access`` classifies the blast radius for policy checks; ``audience`` limits who may be granted it
    (selectors: ``*``, ``agent:<name>``, ``team:<team>``, ``role:<role>``, ``tier:<tier>``).
    """

    id: str
    kind: ToolKind
    description: str = ""
    access: Literal["read", "write"] = "read"
    audience: list[str] = Field(default_factory=lambda: ["*"])
    data_classification: str | None = None
    user_context: bool = Field(
        default=False,
        description="The tool acts as the signed-in person (e.g. Work IQ: their mail, meetings, files). It only works "
        "when a person is talking to the agent, and each person needs a Microsoft 365 Copilot licence.",
    )

    # kind-specific settings. Values may use ${var:NAME} / ${env:NAME} substitution.
    server_url: str | None = None
    server_label: str | None = None
    connection: str | None = Field(default=None, description="Foundry project connection name or id.")
    allowed_tools: list[str] | None = None
    require_approval: Literal["always", "never"] | dict[Literal["always", "never"], list[str]] = "never"
    headers: dict[str, str] | None = None
    index_name: str | None = None
    query_type: str | None = None
    top_k: int | None = None
    openapi_spec: str | None = Field(default=None, description="Path (relative to org dir) to an OpenAPI spec.")
    openapi_auth: dict[str, Any] = Field(
        default_factory=lambda: {"type": "anonymous"},
        description="Foundry OpenAPI auth block, e.g. {type: managed_identity, security_scheme: {audience: ...}}.",
    )
    base_url: str | None = None
    definition: dict[str, Any] | None = Field(default=None, description="Raw Foundry tool JSON for kind=raw.")

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return _check_name(value)

    @model_validator(mode="after")
    def _kind_requirements(self) -> ToolDef:
        required: dict[str, tuple[str, ...]] = {
            "mcp": ("server_url",),
            "openapi": ("openapi_spec",),
            "azure_ai_search": ("connection", "index_name"),
            "sharepoint": ("connection",),
            "fabric_data_agent": ("connection",),
            "fabric_iq": ("connection",),
            "work_iq": ("connection",),
            "bing_grounding": ("connection",),
            "a2a": ("base_url",),
            "raw": ("definition",),
        }
        missing = [f for f in required.get(self.kind, ()) if getattr(self, f) in (None, "")]
        if missing:
            raise ValueError(f"tool '{self.id}' of kind '{self.kind}' requires: {', '.join(missing)}")
        return self


class KnowledgeDef(_Model):
    """A knowledge source. Knowledge is a read-only tool with an audience, so the same SharePoint
    library or search index can be shared org-wide, by a team, or kept private to one agent."""

    id: str
    description: str = ""
    kind: Literal["sharepoint", "azure_ai_search", "fabric_data_agent", "fabric_iq", "work_iq"]
    connection: str
    index_name: str | None = None
    query_type: str | None = None
    top_k: int | None = None
    audience: list[str] = Field(default_factory=lambda: ["*"])
    data_classification: str | None = None
    user_context: bool | None = Field(
        default=None,
        description="Searches as the signed-in person (permission-trimmed). Defaults to true for sharepoint and "
        "work_iq, which Foundry runs on behalf of the user; index into Azure AI Search for unattended agents.",
    )

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return _check_name(value)

    def as_tool(self) -> ToolDef:
        return ToolDef(
            id=self.id,
            kind=self.kind,
            description=self.description,
            access="read",
            audience=self.audience,
            data_classification=self.data_classification,
            connection=self.connection,
            index_name=self.index_name,
            query_type=self.query_type,
            top_k=self.top_k,
            user_context=self.user_context if self.user_context is not None else self.kind in ("sharepoint", "work_iq"),
        )


# --------------------------------------------------------------------------------------------------
# Roles and agents
# --------------------------------------------------------------------------------------------------


class RoleDef(_Model):
    """A reusable archetype. Agents pick a role and inherit its instructions, tools and defaults."""

    id: str
    tier: Tier
    description: str = ""
    instructions: list[InstructionSource] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    model: str | None = None
    temperature: float | None = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return _check_name(value)


class AgentSpec(_Model):
    """One agent. The only file a business user needs to write to add an agent to the hive."""

    api_version: Literal["hivemind/v1"] = Field(default="hivemind/v1", alias="apiVersion")
    kind: Literal["Agent"] = "Agent"

    name: str
    display_name: str | None = None
    description: str
    role: str
    team: str | None = None
    owner: str | None = Field(default=None, description="Accountable human (UPN/email).")

    # Position in the hierarchy
    reports_to: str | None = None
    reviewed_by: str | None = None
    collaborates_with: list[str] = Field(default_factory=list)

    # Behaviour
    model: str | None = None
    temperature: float | None = None
    instructions: list[InstructionSource] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)

    # Why this agent exists - ids from `goals` in hivemind.yaml
    goals: list[str] = Field(default_factory=list)

    # How far it may act on its own, and how that is earned
    autonomy: Autonomy = Field(default_factory=Autonomy)
    evals: EvalSpec = Field(default_factory=EvalSpec)

    # Operations
    channels: Channels = Field(default_factory=Channels)
    schedules: list[Schedule] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    tags: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _check_name(value)

    @property
    def title(self) -> str:
        return self.display_name or self.name.replace("-", " ").title()


# --------------------------------------------------------------------------------------------------
# Organisation
# --------------------------------------------------------------------------------------------------


class TeamDef(_Model):
    description: str = ""
    instructions: list[InstructionSource] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


class OrgMemory(_Model):
    """Models for the per-agent Foundry memory stores."""

    chat_model: str | None = None
    embedding_model: str | None = None


class SharePointConfig(_Model):
    """The HiveMind SharePoint site, where non-technical people shape the agents' rules.

    ``rules_library`` holds Word/markdown rules by convention: Organisation/, Roles/<role>/, Teams/<team>/,
    Agents/<agent>/. Every file is added to the matching instruction layer.
    """

    site: str = Field(description="e.g. contoso.sharepoint.com/sites/HiveMind")
    rules_library: str = "Agent Rules"


class GoalDef(_Model):
    """An organisational goal. Agents and processes link to goals so every piece of work carries its 'why'."""

    title: str
    description: str = ""
    parent: str | None = None
    owner: str | None = None


class Defaults(_Model):
    model: str
    temperature: float | None = None
    instructions: list[InstructionSource] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


class ModelPolicy(_Model):
    allowed: list[str] = Field(default_factory=list, description="Empty means any deployment is allowed.")
    pricing: dict[str, ModelPrice] = Field(default_factory=dict)


class ModelPrice(_Model):
    input_per_1m: float
    output_per_1m: float


ModelPolicy.model_rebuild()


class EvalGate(_Model):
    """Evidence an agent needs before it may run at an autonomy level."""

    min_cases: int = Field(default=0, ge=0)
    pass_rate: float = Field(default=0.0, ge=0, le=1)
    require_approver: bool = False


def _default_gates() -> dict[AutonomyLevel, EvalGate]:
    return {
        AutonomyLevel.SHADOW: EvalGate(),
        AutonomyLevel.SUGGEST: EvalGate(min_cases=5, pass_rate=0.7),
        AutonomyLevel.APPROVE: EvalGate(min_cases=10, pass_rate=0.8, require_approver=True),
        AutonomyLevel.AUTONOMOUS: EvalGate(min_cases=30, pass_rate=0.95, require_approver=True),
        AutonomyLevel.AUTONOMOUS_SAMPLED: EvalGate(min_cases=30, pass_rate=0.95, require_approver=True),
    }


class EvalPolicy(_Model):
    evaluators: list[str] = Field(
        default_factory=lambda: ["builtin.task_adherence", "builtin.intent_resolution"],
        description="Foundry evaluators applied to every agent's datasets and traces.",
    )
    judge_model: str | None = Field(default=None, description="Model deployment used by AI-assisted evaluators.")
    trace_lookback_hours: int = Field(default=24, ge=1)
    trace_sample: int = Field(default=50, ge=1)
    gates: dict[AutonomyLevel, EvalGate] = Field(default_factory=_default_gates)


class Policies(_Model):
    require_owner: bool = True
    require_budget: bool = False
    require_reviewer_for_write_tools: bool = True
    max_hierarchy_depth: int = 6
    evals: EvalPolicy = Field(default_factory=EvalPolicy)


class Environment(_Model):
    """A deployment target. ``variables`` feed ``${var:NAME}`` substitution in catalog files so the
    same spec can point at dev and prod connections."""

    provider: Literal["foundry", "local"] = "foundry"
    deploy_engine: Literal["azd", "sdk"] = Field(
        default="azd",
        description="'azd': generate azure.yaml and run `azd deploy` (agents, routines, evals, processes). "
        "'sdk': deploy prompt agents directly with azure-ai-projects (no processes or eval services).",
    )
    max_autonomy: AutonomyLevel = Field(
        default=AutonomyLevel.AUTONOMOUS_SAMPLED,
        description="Ceiling for every agent in this environment, e.g. keep a pilot tenant at 'approve'.",
    )
    model_deployments: list[ModelDeployment] = Field(
        default_factory=list, description="Model deployments azd upserts on the Foundry project."
    )
    protected: bool = Field(
        default=False,
        description="Changes only through CI (pull request + approval). The admin console allows read-only commands.",
    )
    endpoint: str | None = None
    name_prefix: str = ""
    variables: dict[str, str] = Field(default_factory=dict)
    model_overrides: dict[str, str] = Field(default_factory=dict)
    disabled_agents: list[str] = Field(default_factory=list)
    sharepoint_site: str | None = Field(default=None, description="Overrides sharepoint.site for this environment.")
    sharepoint_local_dir: str | None = Field(
        default=None,
        description="Use a local folder (relative to the org dir) instead of SharePoint - for local runs and tests.",
    )


class ModelDeployment(_Model):
    name: str
    model: str
    version: str
    format: str = "OpenAI"
    sku: str = "GlobalStandard"
    capacity: int = 50


Environment.model_rebuild()


class OrgInfo(_Model):
    name: str
    description: str = ""


class OrgConfig(_Model):
    api_version: Literal["hivemind/v1"] = Field(default="hivemind/v1", alias="apiVersion")
    org: OrgInfo
    defaults: Defaults
    teams: dict[str, TeamDef] = Field(default_factory=dict)
    goals: dict[str, GoalDef] = Field(default_factory=dict)
    sharepoint: SharePointConfig | None = None
    memory: OrgMemory = Field(default_factory=OrgMemory)
    models: ModelPolicy = Field(default_factory=ModelPolicy)
    policies: Policies = Field(default_factory=Policies)
    environments: dict[str, Environment] = Field(default_factory=lambda: {"local": Environment(provider="local")})


# --------------------------------------------------------------------------------------------------
# Processes
# --------------------------------------------------------------------------------------------------


class ProcessTrigger(_Model):
    """Start the process on a schedule (a Foundry routine invoking the process host)."""

    name: str
    cron: str
    time_zone: str = "UTC"
    input: str
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _check_name(value)


class ProcessSpec(_Model):
    """An end-to-end business process run by agents, with people at the approval points.

    The orchestration itself is a Microsoft Agent Framework declarative workflow (``workflow``, a YAML file in
    Microsoft's format). HiveMind validates it against the org (agents exist, autonomy rules, approval steps),
    rewrites agent names per environment and deploys it as a Foundry hosted agent.
    """

    api_version: Literal["hivemind/v1"] = Field(default="hivemind/v1", alias="apiVersion")
    kind: Literal["Process"] = "Process"

    name: str
    display_name: str | None = None
    description: str
    owner: str | None = None
    goal: str | None = None
    workflow: str = Field(description="Path (relative to the org directory) to the Agent Framework workflow YAML.")
    triggers: list[ProcessTrigger] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _check_name(value)

    @property
    def title(self) -> str:
        return self.display_name or self.name.replace("-", " ").title()
