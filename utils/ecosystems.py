"""
Ecosystem detection utility.

Determines which blockchain ecosystem a token belongs to based on
its symbol (highest priority), chain field, or CoinPaprika tags.
"""

# Well-known token symbols → canonical ecosystem (most reliable lookup)
_SYMBOL_ECOSYSTEM_MAP: dict[str, str] = {
    # AI / Compute
    "TAO": "AI Infra", "FET": "AI Infra", "OCEAN": "AI Infra",
    "AGIX": "AI Infra", "NMR": "AI Infra", "GRT": "Ethereum",
    "ARKM": "Ethereum", "VIRTUAL": "Base", "AI16Z": "Solana",
    # DePIN
    "RNDR": "Ethereum", "RENDER": "Ethereum", "AKT": "Cosmos",
    "HNT": "Solana", "FIL": "IPFS", "AR": "Arweave",
    "IO": "Solana", "MOBILE": "Solana", "HONEY": "Solana",
    "GEODNET": "Ethereum", "HIVE": "Hive",
    # Ethereum-native
    "ETH": "Ethereum", "WETH": "Ethereum", "STETH": "Ethereum",
    "WBTC": "Ethereum", "LINK": "Ethereum", "ENS": "Ethereum",
    "LDO": "Ethereum", "AAVE": "Ethereum", "UNI": "Ethereum",
    "MKR": "Ethereum", "CRV": "Ethereum", "SNX": "Ethereum",
    "COMP": "Ethereum", "BAL": "Ethereum", "YFI": "Ethereum",
    "SUSHI": "Ethereum", "1INCH": "Ethereum", "RPL": "Ethereum",
    "CVX": "Ethereum", "PENDLE": "Ethereum", "EIGEN": "Ethereum",
    "ENA": "Ethereum", "ETHFI": "Ethereum", "WEETH": "Ethereum",
    # RWA
    "ONDO": "Ethereum", "PLUME": "Ethereum", "PAXG": "Ethereum",
    "CFG": "Ethereum", "MPL": "Ethereum", "EULER": "Ethereum",
    # Layer 2 tokens
    "ARB": "Arbitrum", "GMX": "Arbitrum", "MAGIC": "Arbitrum",
    "OP": "Optimism", "VELO": "Optimism",
    "STRK": "StarkNet", "ZK": "zkSync", "MANTA": "Ethereum",
    "METIS": "Metis", "IMX": "Ethereum", "LOOPRING": "Ethereum",
    # Base ecosystem
    "BRETT": "Base", "TOSHI": "Base", "AERO": "Base",
    "WELL": "Base", "MORPHO": "Base",
    # Solana ecosystem
    "SOL": "Solana", "JTO": "Solana", "JUP": "Solana",
    "BONK": "Solana", "WIF": "Solana", "POPCAT": "Solana",
    "RAY": "Solana", "PYTH": "Solana", "W": "Solana",
    "TNSR": "Solana", "DRIFT": "Solana", "MOODENG": "Solana",
    "BOME": "Solana", "MEW": "Solana",
    # Cosmos
    "ATOM": "Cosmos", "OSMO": "Cosmos", "INJ": "Cosmos",
    "TIA": "Cosmos", "DYDX": "Cosmos", "SEI": "Cosmos",
    "KAVA": "Cosmos", "SCRT": "Cosmos",
    # BNB Chain
    "BNB": "BNB Chain", "CAKE": "BNB Chain", "BSW": "BNB Chain",
    # Polygon
    "MATIC": "Polygon", "POL": "Polygon",
    # Avalanche
    "AVAX": "Avalanche", "JOE": "Avalanche",
    # Other L1s
    "SUI": "Sui", "APT": "Aptos", "NEAR": "NEAR",
    "TON": "TON", "TRX": "TRON", "XLM": "Stellar",
    "ALGO": "Algorand", "DOT": "Polkadot", "KSM": "Polkadot",
    "ADA": "Cardano", "SOL": "Solana",
    # New / notable
    "BERA": "Berachain", "BGT": "Berachain", "HONEY_B": "Berachain",
    "MON": "Monad", "HYPE": "Hyperliquid",
}

# Chain identifier → canonical ecosystem name
_CHAIN_MAP: dict[str, str] = {
    "solana":               "Solana",
    "base":                 "Base",
    "ethereum":             "Ethereum",
    "cosmos":               "Cosmos",
    "osmosis":              "Cosmos",
    "injective":            "Cosmos",
    "sei":                  "Cosmos",
    "binance-smart-chain":  "BNB Chain",
    "bsc":                  "BNB Chain",
    "bnb":                  "BNB Chain",
    "avalanche":            "Avalanche",
    "avax":                 "Avalanche",
    "polygon":              "Polygon",
    "matic":                "Polygon",
    "arbitrum":             "Arbitrum",
    "optimism":             "Optimism",
    "zksync":               "zkSync",
    "starknet":             "StarkNet",
    "scroll":               "Scroll",
    "linea":                "Linea",
    "blast":                "Blast",
    "sui":                  "Sui",
    "aptos":                "Aptos",
    "near":                 "NEAR",
    "ton":                  "TON",
    "tron":                 "TRON",
    "berachain":            "Berachain",
    "hyperliquid":          "Hyperliquid",
    "monad":                "Monad",
}

# CoinPaprika / keyword tags → ecosystem override
_TAG_ECOSYSTEM_MAP: dict[str, str] = {
    "solana-ecosystem":   "Solana",
    "base-ecosystem":     "Base",
    "ethereum-ecosystem": "Ethereum",
    "cosmos-ecosystem":   "Cosmos",
    "bnb-chain":          "BNB Chain",
    "avalanche-ecosystem":"Avalanche",
    "polygon-ecosystem":  "Polygon",
    "arbitrum-ecosystem": "Arbitrum",
    "optimism-ecosystem": "Optimism",
    "zksync-ecosystem":   "zkSync",
    "sui-ecosystem":      "Sui",
    "aptos-ecosystem":    "Aptos",
}


def detect_ecosystem(chain: str, tags: list[str] | None = None, symbol: str = "") -> str:
    """
    Return the canonical ecosystem name for a token.

    Priority:
      1. Known token symbol (hardcoded map — most authoritative)
      2. CoinPaprika ecosystem tags
      3. chain field from DEX data
      4. "Multi-chain" if chain == "multiple"
      5. "Unknown"
    """
    # 1. Symbol lookup (fastest, most precise)
    if symbol:
        eco = _SYMBOL_ECOSYSTEM_MAP.get(symbol.upper())
        if eco:
            return eco

    # 2. Tags take priority over raw chain field
    for tag in (tags or []):
        eco = _TAG_ECOSYSTEM_MAP.get(tag.lower())
        if eco:
            return eco

    # 3. Chain field
    chain_lower = (chain or "").lower().strip()
    eco = _CHAIN_MAP.get(chain_lower)
    if eco:
        return eco

    # Partial matches for compound chain names (e.g. "ethereum-mainnet")
    for key, val in _CHAIN_MAP.items():
        if key in chain_lower:
            return val

    # 4. Special cases
    if chain_lower == "multiple":
        return "Multi-chain"
    if chain_lower in ("", "unknown"):
        return "Unknown"

    # Capitalise unknown chains for display
    return chain_lower.replace("-", " ").title()
