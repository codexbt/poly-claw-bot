# ============================================================
#  Config Module - SwissTony Bot Configuration
#  Loads from .env and provides typed configuration
# ============================================================

import os
from pathlib import Path
from typing import Optional, List
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

# Load .env file
load_dotenv()


class WalletConfig(BaseModel):
    """Wallet configuration"""
    private_key: str = Field(default_factory=lambda: os.getenv("PRIVATE_KEY", ""))
    chain_id: int = Field(default_factory=lambda: int(os.getenv("CHAIN_ID", "137")))
    signature_type: int = Field(default_factory=lambda: int(os.getenv("SIGNATURE_TYPE", "1")))
    wallet_address: str = Field(default_factory=lambda: os.getenv("WALLET_ADDRESS", ""))
    

class PolymarketAPIConfig(BaseModel):
    """Polymarket API configuration"""
    api_key: str = Field(default_factory=lambda: os.getenv("API_KEY", ""))
    api_secret: str = Field(default_factory=lambda: os.getenv("API_SECRET", ""))
    api_passphrase: str = Field(default_factory=lambda: os.getenv("API_PASSPHRASE", ""))
    host: str = Field(default_factory=lambda: os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com"))
    

class RelayerConfig(BaseModel):
    """Relayer API for gasless transactions"""
    api_key: str = Field(default_factory=lambda: os.getenv("RELAYER_API_KEY", ""))
    signer_address: str = Field(default_factory=lambda: os.getenv("RELAYER_API_KEY_ADDRESS", ""))


class MarketMakerConfig(BaseModel):
    """Market Making Module Configuration"""
    # Spread around mid-price (e.g., 0.005 = 0.5%)
    spread_pct: float = Field(default=0.005)
    
    # Max position size per market (% of balance)
    max_position_pct: float = Field(default=0.10)
    
    # Max total exposure across all markets
    max_total_exposure_pct: float = Field(default=0.50)
    
    # Order refresh interval (seconds)
    refresh_interval: int = Field(default=10)
    
    # Min liquidity to consider a market
    min_liquidity: float = Field(default=1000.0)
    
    # Price range for quoting
    min_price: float = Field(default=0.05)
    max_price: float = Field(default=0.95)
    
    # Active markets to trade
    markets: List[str] = Field(default_factory=list)


class RealityArbConfig(BaseModel):
    """Reality Arbitrage Module Configuration"""
    # Max price to pay for "known" outcome (< 90 cents)
    max_entry_price: float = Field(default=0.90)
    
    # Wait time for price to move (seconds)
    wait_time_min: int = Field(default=20)
    wait_time_max: int = Field(default=40)
    
    # Exit timeout (seconds)
    exit_timeout: int = Field(default=120)
    
    # TV delay simulation (seconds)
    tv_delay: int = Field(default=15)
    
    # Enable reality arb (requires live feed)
    enabled: bool = Field(default=False)
    
    # Simulated feed for testing
    use_simulated_feed: bool = Field(default=True)


class RiskManagerConfig(BaseModel):
    """Risk Management Configuration"""
    # Global stop-loss (% daily loss)
    daily_loss_limit_pct: float = Field(default=0.05)
    
    # Volatility freeze (price move % in 1 minute)
    volatility_freeze_pct: float = Field(default=0.03)
    
    # Sleep mode after loss streak
    loss_streak_sleep: int = Field(default=3)
    sleep_duration_minutes: int = Field(default=5)
    
    # Max trades per hour
    max_trades_per_hour: int = Field(default=50)
    
    # Position size limits
    min_trade_size: float = Field(default=1.0)
    max_trade_size: float = Field(default=100.0)
    default_trade_size: float = Field(default=10.0)


class TradingConfig(BaseModel):
    """Trading Configuration"""
    # Mode
    dry_run: bool = Field(default_factory=lambda: os.getenv("DRY_RUN", "true").lower() == "true")
    paper_trading: bool = Field(default_factory=lambda: os.getenv("PAPER_TRADING", "false").lower() == "true")
    
    # Balances
    starting_balance: float = Field(default_factory=lambda: float(os.getenv("STARTING_BALANCE", "100.0")))
    initial_bankroll: float = Field(default_factory=lambda: float(os.getenv("INITIAL_BANKROLL", "30.0")))
    
    # Trade sizing
    trade_size: float = Field(default_factory=lambda: float(os.getenv("TRADE_SIZE", "80.0")))
    kelly_fraction: float = Field(default_factory=lambda: float(os.getenv("KELLY_FRACTION", "0.25")))
    
    # Thresholds
    min_ev_threshold: float = Field(default_factory=lambda: float(os.getenv("MIN_EV_THRESHOLD", "0.08")))
    momentum_threshold: float = Field(default_factory=lambda: float(os.getenv("MOMENTUM_THRESHOLD", "0.02")))
    strong_threshold: float = Field(default_factory=lambda: float(os.getenv("STRONG_THRESHOLD", "0.05")))
    
    # Price ranges
    price_min: float = Field(default_factory=lambda: float(os.getenv("PRICE_MIN", "0.88")))
    price_max: float = Field(default_factory=lambda: float(os.getenv("PRICE_MAX", "0.95")))
    
    # Timing
    loop_sec: float = Field(default_factory=lambda: float(os.getenv("LOOP_SEC", "0.1")))
    scan_interval: int = Field(default_factory=lambda: int(os.getenv("SCAN_INTERVAL_SECONDS", "10")))
    
    # Slippage
    slippage_pct: float = Field(default_factory=lambda: float(os.getenv("SLIPPAGE_PCT", "0.20")))
    
    # Limits
    daily_limit: float = Field(default_factory=lambda: float(os.getenv("DAILY_LIMIT", "36000")))
    
    # Hybrid mode
    hybrid_mode: bool = Field(default_factory=lambda: os.getenv("HYBRID_MODE", "false").lower() == "true")
    limit_offset_pct: float = Field(default_factory=lambda: float(os.getenv("LIMIT_OFFSET_PCT", "0.03")))
    limit_wait_sec: int = Field(default_factory=lambda: int(os.getenv("LIMIT_WAIT_SEC", "7")))


class BotConfig(BaseSettings):
    """
    Main Configuration for SwissTony Bot
    Loads all settings from environment variables
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    # Sub-configs
    wallet: WalletConfig = Field(default_factory=WalletConfig)
    polymarket: PolymarketAPIConfig = Field(default_factory=PolymarketAPIConfig)
    relayer: RelayerConfig = Field(default_factory=RelayerConfig)
    market_maker: MarketMakerConfig = Field(default_factory=MarketMakerConfig)
    reality_arb: RealityArbConfig = Field(default_factory=RealityArbConfig)
    risk: RiskManagerConfig = Field(default_factory=RiskManagerConfig)
    trading: TradingConfig = Field(default_factory=TradingConfig)
    
    # OpenRouter for AI analysis
    openrouter_api_key: str = Field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    
    # Telegram notifications
    telegram_bot_token: str = Field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = Field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))
    
    # Logging
    log_level: str = Field(default="INFO")
    log_file: str = Field(default="swissbot.log")
    
    def __init__(self, **data):
        super().__init__(**data)
        # Validate required fields
        self._validate()
    
    def _validate(self):
        """Validate critical configuration"""
        if not self.wallet.private_key:
            raise ValueError("PRIVATE_KEY is required in .env")
        if not self.wallet.wallet_address:
            raise ValueError("WALLET_ADDRESS is required in .env")
        if not self.polymarket.api_key:
            raise ValueError("API_KEY is required in .env")
    
    @property
    def is_live(self) -> bool:
        """Check if running in live mode"""
        return not self.trading.dry_run and not self.trading.paper_trading
    
    def to_dict(self) -> dict:
        """Convert to dictionary (masking sensitive data)"""
        data = self.model_dump()
        # Mask sensitive fields
        if data.get("wallet"):
            data["wallet"]["private_key"] = "***" if data["wallet"].get("private_key") else ""
        if data.get("polymarket"):
            data["polymarket"]["api_secret"] = "***" if data["polymarket"].get("api_secret") else ""
            data["polymarket"]["api_passphrase"] = "***" if data["polymarket"].get("api_passphrase") else ""
        return data


# Global config instance
_config: Optional[BotConfig] = None


def get_config() -> BotConfig:
    """Get or create global config instance"""
    global _config
    if _config is None:
        _config = BotConfig()
    return _config


def reload_config() -> BotConfig:
    """Reload configuration from .env"""
    global _config
    _config = BotConfig()
    return _config