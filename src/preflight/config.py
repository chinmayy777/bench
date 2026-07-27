"""Environment-driven configuration. Cash-free by design: mainnet spending is
hard-disabled, with ONE deliberate, opt-in exception — X Layer (eip155:196),
gated behind XLAYER_SPENDING_ENABLED (default false) and its own hard caps
and dedicated key. See payer.py for the spend logic."""
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
    # Embedded replica needs a real local file — libsql's WAL mode can't
    # attach to ":memory:" (fails with "wal_insert_begin failed").
    turso_replica_path: str = os.getenv("TURSO_REPLICA_PATH", "/tmp/tender-replica.db")
    payer_mode: str = os.getenv("PAYER_MODE", "mock")  # off | mock | testnet
    payer_private_key: str = os.getenv("PAYER_PRIVATE_KEY", "")
    allow_local_targets: bool = _flag("ALLOW_LOCAL_TARGETS")
    kill_switch: bool = _flag("PAYER_KILL_SWITCH")
    max_pay_per_call_usdt: float = float(os.getenv("MAX_PAY_PER_CALL_USDT", "2"))
    max_pay_per_day_usdt: float = float(os.getenv("MAX_PAY_PER_DAY_USDT", "10"))
    run_budget_s: float = float(os.getenv("RUN_BUDGET_S", "75"))
    wake_enabled: bool = _flag("WAKE_ENABLED", "1")
    wake_timeout_s: float = float(os.getenv("WAKE_TIMEOUT_S", "75"))

    # --- X Layer (eip155:196) mainnet spending — opt-in, capped, separate key ---
    # Must be explicitly "true"; every other network above stays cash-free
    # regardless of this flag. See MAINNET_NETWORKS below — eip155:196 is
    # deliberately the only mainnet ever removed from that hard-block set.
    xlayer_spending_enabled: bool = _flag("XLAYER_SPENDING_ENABLED")
    # Dedicated key — never the same EOA/funds as PAYER_PRIVATE_KEY (testnet/mock).
    xlayer_payer_private_key: str = os.getenv("XLAYER_PAYER_PRIVATE_KEY", "")
    # Conservative defaults; all three are hard caps (§payer.py Payer._pay_xlayer) —
    # exceeding any one refuses that purchase outright, never a silent skip.
    max_xlayer_pay_per_call_usdt: float = float(os.getenv("MAX_XLAYER_PAY_PER_CALL_USDT", "1"))
    max_xlayer_pay_per_run_usdt: float = float(os.getenv("MAX_XLAYER_PAY_PER_RUN_USDT", "3"))
    max_xlayer_pay_per_day_usdt: float = float(os.getenv("MAX_XLAYER_PAY_PER_DAY_USDT", "10"))

    # Networks we will ever sign payments on. Mainnets are NOT listable here by
    # design — except eip155:196, appended in __post_init__ only when
    # xlayer_spending_enabled is true, so C4/classify_payability only ever
    # reports it "payable" when the operator actually opted in.
    allowed_pay_networks: tuple = field(
        default=("base-sepolia", "eip155:84532", "mock"), init=False
    )

    def __post_init__(self) -> None:
        if self.xlayer_spending_enabled:
            object.__setattr__(self, "allowed_pay_networks",
                               self.allowed_pay_networks + ("eip155:196",))


settings = Settings()

# eip155:196 (X Layer) is deliberately absent — it's the one mainnet with its
# own opt-in, capped path (payer.py Payer._pay_xlayer), never this blanket ban.
MAINNET_NETWORKS = {"xlayer", "x-layer", "eip155:8453", "base"}

XLAYER_NETWORK = "eip155:196"
XLAYER_USDT0_ASSET = "0x779ded0c9e1022225f8e0630b35a9b54be713736"
