# HiveMind process host: an Agent Framework declarative workflow served as a Foundry hosted agent.
FROM python:3.12-slim

# Power Fx (used by workflow expressions such as `=Local.result`) runs on the .NET runtime.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates libicu72 \
 && curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \
 && bash /tmp/dotnet-install.sh --channel 8.0 --runtime dotnet --install-dir /usr/share/dotnet \
 && ln -s /usr/share/dotnet/dotnet /usr/bin/dotnet \
 && rm /tmp/dotnet-install.sh \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
ENV DOTNET_ROOT=/usr/share/dotnet

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8088
CMD ["python", "main.py"]
