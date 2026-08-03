FROM python:3.14-slim

ARG TARGETARCH=amd64

RUN set -eux; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends ca-certificates curl; \
    latest() { curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$1/releases/latest" | sed 's|.*/v||'; }; \
    gh_version="$(latest cli/cli)"; \
    age_version="$(latest FiloSottile/age)"; \
    curl -fsSL --retry 3 --retry-all-errors \
      "https://github.com/cli/cli/releases/download/v${gh_version}/gh_${gh_version}_linux_${TARGETARCH}.tar.gz" \
      | tar xz -C /usr/local/bin --strip-components=2 "gh_${gh_version}_linux_${TARGETARCH}/bin/gh"; \
    curl -fsSL --retry 3 --retry-all-errors \
      "https://github.com/FiloSottile/age/releases/download/v${age_version}/age-v${age_version}-linux-${TARGETARCH}.tar.gz" \
      | tar xz -C /usr/local/bin --strip-components=1 age/age; \
    apt-get purge -y -qq curl; \
    apt-get autoremove -y -qq; \
    rm -rf /var/lib/apt/lists/*; \
    gh --version; \
    age --version

RUN useradd --create-home --uid 1000 nested

WORKDIR /app
COPY keys/ keys/
COPY nested_runner/ nested_runner/

USER nested
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "-m", "nested_runner"]
