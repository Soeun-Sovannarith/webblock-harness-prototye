"""Runtime configuration.

Every environment-specific value (credentials, organisation names, URLs) is read
from the environment; nothing tenant- or environment-specific is hardcoded.
Secrets are held as ``SecretStr`` so they never appear in logs, reprs or API
responses.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigDeliveryMode(StrEnum):
    """How published Puck configuration reaches a client site.

    ``runtime``: the client Next.js app fetches its published configuration from
    the harness public endpoint (``/v1/public/sites/{site_key}/config``), so a
    content-only change needs no source build.

    ``commit``: the configuration is committed to the client repository
    (``CONFIG_REPO_PATH``) and shipped through the normal build + deploy workflow.
    Use it when the client app bundles its configuration at build time.
    """

    RUNTIME = "runtime"
    COMMIT = "commit"


class DeployProviderKind(StrEnum):
    # "Deployed" = the commit is on the default branch of the client repo in the organisation and its
    # content is verified there. Use until hosting/CI exists.
    GITHUB_PUSH = "github_push"
    GITHUB_ACTIONS = "github_actions"
    FAKE = "fake"


class GitProviderKind(StrEnum):
    GITHUB = "github"
    MEMORY = "memory"


class LLMProviderKind(StrEnum):
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"  # a local model served by Ollama (e.g. qwen2.5-coder:7b)
    NONE = "none"


class Settings(BaseSettings):
    # hide_input_in_errors: validation errors must never echo configured values (tokens, secrets).
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True)

    environment: str = Field(default="development", validation_alias="WEBBLOCK_ENV")

    # Persistence ----------------------------------------------------------
    database_url: str = Field(default="sqlite:///./var/harness.db", validation_alias="DATABASE_URL")
    artifact_dir: Path = Field(default=Path("./var/artifacts"), validation_alias="ARTIFACT_DIR")

    # Authentication (identity is issued by the WebBlock gateway / Spring Boot auth service)
    auth_jwt_secret: SecretStr | None = Field(default=None, validation_alias="AUTH_JWT_SECRET")
    auth_jwt_public_key: str | None = Field(default=None, validation_alias="AUTH_JWT_PUBLIC_KEY")
    auth_jwt_algorithms: list[str] = Field(default=["HS256"], validation_alias="AUTH_JWT_ALGORITHMS")
    auth_jwt_issuer: str | None = Field(default=None, validation_alias="AUTH_JWT_ISSUER")
    auth_jwt_audience: str | None = Field(default=None, validation_alias="AUTH_JWT_AUDIENCE")
    auth_tenant_claim: str = Field(default="tenant_id", validation_alias="AUTH_TENANT_CLAIM")
    auth_roles_claim: str = Field(default="roles", validation_alias="AUTH_ROLES_CLAIM")

    # GitHub ---------------------------------------------------------------
    git_provider: GitProviderKind = Field(default=GitProviderKind.GITHUB, validation_alias="GIT_PROVIDER")
    github_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GITHUB_TOKEN", "GITHUB_ORGANIZATION_KEY"),
    )
    github_org: str | None = Field(default=None, validation_alias=AliasChoices("GITHUB_ORG", "GITHUB_ORGANIZATION_NAME"))
    github_api_url: str = Field(default="https://api.github.com", validation_alias="GITHUB_API_URL")
    harness_branch_prefix: str = Field(default="webblock/job-", validation_alias="HARNESS_BRANCH_PREFIX")
    harness_commit_author_name: str = Field(default="WebBlock Harness", validation_alias="HARNESS_COMMIT_AUTHOR_NAME")
    harness_commit_author_email: str = Field(default="harness@webblock.invalid", validation_alias="HARNESS_COMMIT_AUTHOR_EMAIL")

    # Deployment -----------------------------------------------------------
    deploy_provider: DeployProviderKind = Field(default=DeployProviderKind.GITHUB_PUSH, validation_alias="DEPLOY_PROVIDER")
    deploy_workflow_file: str = Field(default="deploy.yml", validation_alias="DEPLOY_WORKFLOW_FILE")
    # e.g. "https://{repo_name}.webblock.app/api/webblock/version" – must report the deployed commit SHA.
    deploy_verify_url_template: str | None = Field(default=None, validation_alias="DEPLOY_VERIFY_URL_TEMPLATE")
    # e.g. "https://{repo_name}.webblock.app/api/webblock/revalidate" – called after runtime config publish.
    runtime_revalidate_url_template: str | None = Field(default=None, validation_alias="RUNTIME_REVALIDATE_URL_TEMPLATE")
    runtime_revalidate_secret: SecretStr | None = Field(default=None, validation_alias="RUNTIME_REVALIDATE_SECRET")
    preview_url_template: str | None = Field(default=None, validation_alias="PREVIEW_URL_TEMPLATE")
    config_delivery_mode: ConfigDeliveryMode = Field(default=ConfigDeliveryMode.COMMIT, validation_alias="CONFIG_DELIVERY_MODE")
    config_repo_path: str = Field(default="webblock/site-config.json", validation_alias="CONFIG_REPO_PATH")

    # CI validation of source patches (check-run names on the job branch) --
    required_checks_frontend: list[str] = Field(
        default=["format", "typecheck", "lint", "test", "build"], validation_alias="REQUIRED_CHECKS_FRONTEND"
    )
    required_checks_backend: list[str] = Field(default=["integration-tests"], validation_alias="REQUIRED_CHECKS_BACKEND")
    required_checks_database: list[str] = Field(default=["migration-test"], validation_alias="REQUIRED_CHECKS_DATABASE")
    required_checks_config_commit: list[str] = Field(default=[], validation_alias="REQUIRED_CHECKS_CONFIG_COMMIT")

    # Polling / timeouts ----------------------------------------------------
    poll_interval_seconds: float = Field(default=10.0, validation_alias="POLL_INTERVAL_SECONDS")
    ci_timeout_seconds: float = Field(default=1800.0, validation_alias="CI_TIMEOUT_SECONDS")
    deploy_timeout_seconds: float = Field(default=1800.0, validation_alias="DEPLOY_TIMEOUT_SECONDS")
    project_lock_ttl_seconds: float = Field(default=3600.0, validation_alias="PROJECT_LOCK_TTL_SECONDS")

    # LLM ------------------------------------------------------------------
    llm_provider: LLMProviderKind = Field(default=LLMProviderKind.ANTHROPIC, validation_alias="LLM_PROVIDER")
    llm_model: str = Field(default="claude-opus-5-5", validation_alias="LLM_MODEL")
    llm_effort: str = Field(default="high", validation_alias="LLM_EFFORT")
    llm_max_tokens: int = Field(default=64000, validation_alias="LLM_MAX_TOKENS")
    ollama_base_url: str = Field(default="http://localhost:11434", validation_alias="OLLAMA_BASE_URL")
    # Context window to request from Ollama (its own default is small; the site config + registry need room).
    ollama_num_ctx: int = Field(default=32768, ge=2048, validation_alias="OLLAMA_NUM_CTX")
    ollama_timeout_seconds: float = Field(default=600.0, gt=0, validation_alias="OLLAMA_TIMEOUT_SECONDS")

    # Source patch policy ---------------------------------------------------
    max_patch_files: int = Field(default=25, validation_alias="MAX_PATCH_FILES")
    max_patch_changed_lines: int = Field(default=2500, validation_alias="MAX_PATCH_CHANGED_LINES")
    max_file_bytes: int = Field(default=200_000, validation_alias="MAX_FILE_BYTES")
    generated_code_env_allowlist: list[str] = Field(
        default=["DATABASE_URL", "NEXT_PUBLIC_*"], validation_alias="GENERATED_CODE_ENV_ALLOWLIST"
    )

    # Job execution ---------------------------------------------------------
    job_execution: str = Field(default="background", validation_alias="JOB_EXECUTION")  # background | inline
    job_workers: int = Field(default=4, validation_alias="JOB_WORKERS")

    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    # New client sites --------------------------------------------------------
    new_repo_private: bool = Field(default=True, validation_alias="NEW_REPO_PRIVATE")
    new_repo_suffix: str = Field(default="-site", validation_alias="NEW_REPO_SUFFIX")
    # Business Portal tokens (Ed25519). Production: PORTAL_SIGNING_KEY (PEM). Dev: generated into PORTAL_KEY_FILE.
    portal_signing_key: SecretStr | None = Field(default=None, validation_alias="PORTAL_SIGNING_KEY")
    portal_key_file: Path = Field(default=Path("./var/portal_signing_key.pem"), validation_alias="PORTAL_KEY_FILE")
    # Where a site runs before it has a WebBlock subdomain (used to open its Business Portal).
    default_site_url: str = Field(default="http://localhost:3000", validation_alias="DEFAULT_SITE_URL")
    # Local-only sign-in for the bundled web app. Never enable in production: real identities come from the auth service.
    dev_login_enabled: bool = Field(default=False, validation_alias="DEV_LOGIN_ENABLED")

    @model_validator(mode="after")
    def _check_auth(self) -> Settings:
        if self.auth_jwt_secret is None and self.auth_jwt_public_key is None:
            raise ValueError("Configure AUTH_JWT_SECRET (HS*) or AUTH_JWT_PUBLIC_KEY (RS*/ES*) to verify identities.")
        allowed = {"HS256", "HS384", "HS512", "RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"}
        if not self.auth_jwt_algorithms or not set(self.auth_jwt_algorithms) <= allowed:
            raise ValueError(f"AUTH_JWT_ALGORITHMS must be a non-empty subset of {sorted(allowed)}.")
        hmac = any(a.startswith("HS") for a in self.auth_jwt_algorithms)
        if self.auth_jwt_public_key and hmac:
            # Prevents algorithm confusion (a public key accepted as an HMAC secret).
            raise ValueError('AUTH_JWT_PUBLIC_KEY requires asymmetric AUTH_JWT_ALGORITHMS (e.g. ["RS256"]).')
        if not self.auth_jwt_public_key and not hmac:
            raise ValueError("AUTH_JWT_SECRET requires HS* AUTH_JWT_ALGORITHMS.")
        if (
            self.auth_jwt_secret is not None
            and not self.auth_jwt_public_key
            and len(self.auth_jwt_secret.get_secret_value()) < 32
        ):
            raise ValueError("AUTH_JWT_SECRET must be at least 32 characters.")
        if self.job_execution not in {"background", "inline"}:
            raise ValueError("JOB_EXECUTION must be 'background' or 'inline'.")
        return self

    def required_checks_for(self, scopes: set[str]) -> list[str]:
        checks: list[str] = []
        if "frontend" in scopes or not scopes:
            checks += self.required_checks_frontend
        if "backend" in scopes:
            checks += self.required_checks_frontend + self.required_checks_backend
        if "database" in scopes:
            checks += self.required_checks_database
        return list(dict.fromkeys(checks))


@lru_cache
def get_settings() -> Settings:
    return Settings()
