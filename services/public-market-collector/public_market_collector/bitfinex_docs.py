"""Citations read from official Bitfinex documentation. No network calls.

Fetched read-only on 2026-11-07. Nothing in this module opens a socket.
"""

from __future__ import annotations

DOCUMENTED_PUBLIC_WS = "wss://api-pub.bitfinex.com/ws/2"
DOCUMENTED_AUTHENTICATED_WS = "wss://api.bitfinex.com/ws/2"
AUTHENTICATED_WS_USED = False

FLAG_TIMESTAMP = 32768
FLAG_SEQ_ALL = 65536
FLAG_OB_CHECKSUM = 131072
FLAG_BULK_UPDATES = 536870912

# Official blog example string (integer prices and amounts).
CHECKSUM_EXAMPLE_STRING = "6000:1:6100:-3:5900:2:6200:-4"
# Signed CRC-32 (zlib / crc-32) of that string, pinned by the tests.
CHECKSUM_EXAMPLE_SIGNED = 1756193398

PRECISIONS_AGG = ("P0", "P1", "P2", "P3", "P4")
PRECISION_RAW = "R0"
FREQS = ("F0", "F1")
LENGTHS = ("1", "25", "100", "250")

CITATIONS = (
    {
        "id": "ws-general",
        "url": "https://docs.bitfinex.com/docs/ws-general",
        "holds": (
            "Public URL wss://api-pub.bitfinex.com/ws/2. Authenticated host is separate. "
            "JSON text. Timestamps that the venue sends are UTC milliseconds. "
            "Heartbeat is [CHANNEL_ID, \"hb\"] about every 15 seconds and the documented "
            "shape has no exchange timestamp. "
            "conf flags: TIMESTAMP 32768, SEQ_ALL 65536, OB_CHECKSUM 131072, "
            "BULK_UPDATES 536870912. SEQ_ALL is described as a beta feature that adds "
            "sequence numbers so a client can see loss or reordering. The page does not "
            "give the array index or say whether the counter is per connection or per channel. "
            "Info codes 20051 reconnect, 20060 maintenance, 20061 maintenance ended. "
            "Public subscription limit 30 channels per connection. "
            "Array length must not be hardcoded; new fields may be appended."
        ),
    },
    {
        "id": "ws-public-books",
        "url": "https://docs.bitfinex.com/reference/ws-public-books",
        "holds": (
            "Aggregated book. prec P0..P4 (default P0), freq F0 realtime or F1 every 2 seconds "
            "(default F0), len \"1\"|\"25\"|\"100\"|\"250\" (default \"25\"). "
            "Snapshot is [CHANNEL_ID, [[PRICE, COUNT, AMOUNT], ...]]. "
            "Update is [CHANNEL_ID, [PRICE, COUNT, AMOUNT]]. "
            "COUNT 0 deletes the level: AMOUNT 1 removes a bid, AMOUNT -1 removes an ask. "
            "COUNT > 0: AMOUNT > 0 bid, AMOUNT < 0 ask. "
            "Documented trading-book fields have no exchange timestamp. "
            "len is the subscribed price-point count, not the whole exchange book."
        ),
    },
    {
        "id": "ws-public-raw-books",
        "url": "https://docs.bitfinex.com/reference/ws-public-raw-books",
        "holds": (
            "prec R0. Entry is [ORDER_ID, PRICE, AMOUNT]. PRICE 0 deletes that order id. "
            "AMOUNT > 0 bid, AMOUNT < 0 ask. "
            "If len is 25 the subscription is 25 orders, not 25 price levels and not the full book. "
            "Same-price checksum order is ascending order id. This is not a historical queue."
        ),
    },
    {
        "id": "ws-public-trades",
        "url": "https://docs.bitfinex.com/reference/ws-public-trades",
        "holds": (
            "Trading snapshot [CHANNEL_ID, [[ID, MTS, AMOUNT, PRICE], ...]]. "
            "Updates [CHANNEL_ID, \"te\"|\"tu\", [ID, MTS, AMOUNT, PRICE]]. "
            "MTS is the venue millisecond timestamp. "
            "AMOUNT > 0 buy, AMOUNT < 0 sell. "
            "te is trade executed and tu is trade updated. Both carry the trade ID on v2. "
            "Funding trades (fte/ftu) are a different shape and are not collected."
        ),
    },
    {
        "id": "ws-websocket-checksum",
        "url": "https://docs.bitfinex.com/docs/ws-websocket-checksum",
        "holds": (
            "CRC32 signed integer over the first 25 bids and 25 asks. "
            "Message [CHANNEL_ID, \"cs\", CHECKSUM] and the checksum value is index 2 "
            "of that documented shape. "
            "Bids descending price, asks ascending price. Raw orders at the same price "
            "sort by id ascending. Values interleave bid, ask, bid, ask and join with ':'. "
            "P-precision uses price and amount. R0 uses order id and amount. "
            "Ask amounts are negative in the documented example string."
        ),
    },
    {
        "id": "blog-checksum",
        "url": "https://blog.bitfinex.com/products/bitfinex-api-order-books-checksums/",
        "holds": (
            "Worked P0 string 6000:1:6100:-3:5900:2:6200:-4. "
            "The snippet omits a side when that index is missing, so books shallower "
            "than 25 are not described as padded. "
            "Checksum failure means the local book is not current: resubscribe or resnapshot."
        ),
    },
    {
        "id": "ws-auth",
        "url": "https://docs.bitfinex.com/docs/ws-auth",
        "holds": (
            "Authenticated host rate limit is 5 connections per 15 seconds. "
            "That host is not used. It is not a public-market limit."
        ),
    },
)

UNKNOWNS = (
    "SEQ_ALL array index is not specified on ws-general. Scope (connection-wide versus per channel) is not specified. Extraction stays off unless a synthetic test passes seq_layout='index_1_unverified', which is not an official layout.",
    "TIMESTAMP flag does not document the array index. Book and heartbeat records keep ts_exchange_ms null. Only a trade MTS inside the documented trade array is copied.",
    "Public reconnect rate limit is not stated on ws-general. The authenticated 5/15s limit is a different host and is not applied. Synthetic OPERATIONAL_VALUES backoff inputs stay unset. Public capture pins 1 s initial, 30 s cap, 0 s jitter, and resets to 1 s after a connection that delivered at least one message.",
    "Checksum numbers use JavaScript Number.toString. The integer blog vector and a fractional vector are pinned. Values that are not finite numbers still fail closed.",
    "Trade ids are not contiguous. A missing id is not treated as a lost trade. Loss is visible only as a reconnect or maintenance gap.",
    "Receive timestamps use the host clock. They are not NTP-disciplined. event_lag_ms and clock gaps include that skew.",
    "Whether a checksum over fewer than 25 levels is zero-padded is not stated. The implementation follows the blog loop and omits missing levels.",
    "Public capture stamps receive time from the host clock via time.time_ns and time.monotonic_ns. It does not query NTP.",
    "v2 tu versus te has one shared trade-array shape on the trades page. The v1 te/tu layout is not used.",
    "Synthetic OPERATIONAL_VALUES stay unset. Public capture pins tBTCF0:USTF0 len 250, a 512 MiB free-space floor, 64 MiB segments and a 1 hour segment age. That is not a board freeze of the synthetic config.",
)
