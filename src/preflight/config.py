"""Environment-driven configuration. Cash-free by design: mainnet spending is hard-disabled."""
import os
from dataclasses import dataclass, field


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    base_url: str = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    db_path: str = os.getenv("DB_PATH", "preflight.db")
    # When both are set, store.py persists comparisons/reports to Turso
    # (libSQL) instead of the local sqlite file, so data survives container
    # restarts. Either absent -> falls back to db_path above, unchanged.
    turso_database_url: str = os.getenv("TURSO_DATABASE_URL", "")
    turso_auth_token: str = os.getenv("TURSO_AUTH_TOKEN", "")
    payer_mode: str = os.getenv("PAYER_MODE", "mock")  # off | mock | testnet
    payer_private_key: str = os.getenv("PAYER_PRIVATE_KEY", "")
    allow_local_targets: bool = _flag("ALLOW_LOCAL_TARGETS")
    kill_switch: bool = _flag("PAYER_KILL_SWITCH")
    max_pay_per_call_usdt: float = float(os.getenv("MAX_PAY_PER_CALL_USDT", "2"))
    max_pay_per_day_usdt: float = float(os.getenv("MAX_PAY_PER_DAY_USDT", "10"))
    run_budget_s: float = float(os.getenv("RUN_BUDGET_S", "75"))
    wake_enabled: bool = _flag("WAKE_ENABLED", "1")
    wake_timeout_s: float = float(os.getenv("WAKE_TIMEOUT_S", "75"))
    # Networks we will ever sign payments on. Mainnets are NOT listable here by design.
    allowed_pay_networks: tuple = field(
        default=("base-sepolia", "eip155:84532", "mock"), init=False
    )


settings = Settings()

MAINNET_NETWORKS = {"eip155:196", "xlayer", "x-layer", "eip155:8453", "base"}
