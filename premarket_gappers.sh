#!/usr/bin/env bash
#
# premarket_gappers.sh — premarket gap scanner
#
# Pulls the Yahoo Finance day-gainers screener, filters for gap/price/volume,
# then looks up a news catalyst per ticker on Benzinga.
#
# Usage:  ./premarket_gappers.sh [-o OUTDIR] [-n TOP_N] [--no-llm]
#
# Requires: curl, jq.  Optional: claude CLI (for one-sentence catalyst
# summaries); without it the script falls back to scraped headlines.

set -uo pipefail

readonly GAINERS_JSON="https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=day_gainers&count=100"
readonly GAINERS_HTML="https://finance.yahoo.com/markets/stocks/gainers/"
readonly BENZINGA_BASE="https://www.benzinga.com/quote"
readonly UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Filter thresholds
readonly MIN_GAP_PCT=5
readonly MIN_PRICE=3
readonly MIN_VOLUME=50000

OUTDIR="."
TOP_N=10
USE_LLM=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) OUTDIR="${2:?-o needs a directory}"; shift 2 ;;
    -n) TOP_N="${2:?-n needs a number}"; shift 2 ;;
    --no-llm) USE_LLM=0; shift ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

for dep in curl jq; do
  command -v "$dep" >/dev/null 2>&1 || die "missing required dependency: $dep"
done

if [[ $USE_LLM -eq 1 ]] && ! command -v claude >/dev/null 2>&1; then
  log "claude CLI not found — catalyst will fall back to first scraped headline"
  USE_LLM=0
fi

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

fetch() {
  # fetch URL -> stdout; non-zero on transport error or non-2xx
  local url="$1" out="$2" code
  code=$(curl -sS -L --compressed --max-time 25 --retry 2 --retry-delay 1 \
           -A "$UA" -H 'Accept-Language: en-US,en;q=0.9' \
           -o "$out" -w '%{http_code}' "$url" 2>>"$WORKDIR/curl.err") || return 1
  [[ "$code" =~ ^2 ]] || { log "HTTP $code for $url"; return 1; }
  [[ -s "$out" ]] || return 1
  return 0
}

html_to_text() {
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$1" <<'PY'
import html, re, sys
raw = open(sys.argv[1], encoding="utf-8", errors="replace").read()
raw = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", raw)
raw = re.sub(r"(?s)<[^>]+>", "\n", raw)
text = html.unescape(raw)
lines = [ln.strip() for ln in text.splitlines()]
print("\n".join(ln for ln in lines if ln)[:20000])
PY
  else
    sed -e 's/<[^>]*>/\n/g' "$1" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | grep -v '^$' | head -c 20000
  fi
}

# ---------------------------------------------------------------- stage 1: rows
log "fetching gainers screener"
ROWS="$WORKDIR/rows.json"
: > "$ROWS"

if fetch "$GAINERS_JSON" "$WORKDIR/gainers.json"; then
  # Prefer premarket fields, fall back to regular-session values when the
  # premarket block is absent (screener omits it once the session opens).
  jq '[ (.finance.result[0].quotes // [])[]
        | { symbol:            (.symbol // empty),
            price:             (.preMarketPrice // .regularMarketPrice // null),
            gap_pct:           (.preMarketChangePercent // .regularMarketChangePercent // null),
            premarket_volume:  (.regularMarketVolume // 0) }
        | select(.symbol != null and .price != null and .gap_pct != null) ]' \
     "$WORKDIR/gainers.json" > "$ROWS" 2>/dev/null || : > "$ROWS"
fi

if [[ ! -s "$ROWS" ]] || [[ "$(jq 'length' "$ROWS" 2>/dev/null || echo 0)" -eq 0 ]]; then
  log "screener API empty — falling back to HTML scrape"
  if fetch "$GAINERS_HTML" "$WORKDIR/gainers.html"; then
    # Yahoo embeds the same quote objects in the page bootstrap payload.
    grep -o '"symbol":"[A-Z.\-]\{1,8\}"[^}]*' "$WORKDIR/gainers.html" 2>/dev/null \
      | python3 -c '
import json, re, sys
seen, out = set(), []
for chunk in sys.stdin:
    sym = re.search(r"\"symbol\":\"([A-Z.\-]{1,8})\"", chunk)
    if not sym or sym.group(1) in seen:
        continue
    def num(key):
        m = re.search(r"\"%s\":\{[^}]*?\"raw\":(-?[\d.]+)" % key, chunk) or \
            re.search(r"\"%s\":(-?[\d.]+)" % key, chunk)
        return float(m.group(1)) if m else None
    price = num("preMarketPrice") or num("regularMarketPrice")
    gap   = num("preMarketChangePercent")
    if gap is None:
        gap = num("regularMarketChangePercent")
    vol   = num("regularMarketVolume") or 0
    if price is None or gap is None:
        continue
    seen.add(sym.group(1))
    out.append({"symbol": sym.group(1), "price": price,
                "gap_pct": gap, "premarket_volume": int(vol)})
json.dump(out, sys.stdout)
' > "$ROWS" 2>/dev/null || : > "$ROWS"
  fi
fi

[[ -s "$ROWS" ]] || die "could not retrieve gainers data from either endpoint (see network access)"

TOTAL=$(jq 'length' "$ROWS")
log "parsed $TOTAL rows"

# ---------------------------------------------------------------- stage 2: filter
FILTERED="$WORKDIR/filtered.json"
jq --argjson g "$MIN_GAP_PCT" --argjson p "$MIN_PRICE" \
   --argjson v "$MIN_VOLUME" --argjson n "$TOP_N" '
  [ .[] | select(.gap_pct > $g and .price > $p and .premarket_volume > $v) ]
  | sort_by(-.gap_pct) | .[0:$n]
  | to_entries | map(.value + {rank: (.key + 1)})' "$ROWS" > "$FILTERED"

COUNT=$(jq 'length' "$FILTERED")
log "$COUNT names passed filters (gap>${MIN_GAP_PCT}%, price>\$${MIN_PRICE}, vol>${MIN_VOLUME})"

# ---------------------------------------------------------------- stage 3: catalyst
CATALYST_PROMPT_TMPL='What recent news or catalyst is driving %s stock today? Return a one-sentence summary, then up to 2 recent headlines verbatim. Just the data — no commentary.'

lookup_catalyst() {
  # $1 = ticker; emits {"catalyst":...,"headlines":[...]} on stdout. Never fails.
  local ticker="$1" page="$WORKDIR/$1.html" txt="$WORKDIR/$1.txt"

  if ! fetch "$BENZINGA_BASE/$ticker" "$page"; then
    log "  $ticker: benzinga fetch failed"
    echo '{"catalyst":null,"headlines":[]}'; return 0
  fi
  html_to_text "$page" > "$txt" 2>/dev/null || { echo '{"catalyst":null,"headlines":[]}'; return 0; }
  [[ -s "$txt" ]] || { echo '{"catalyst":null,"headlines":[]}'; return 0; }

  if [[ $USE_LLM -eq 1 ]]; then
    local prompt result
    printf -v prompt "$CATALYST_PROMPT_TMPL" "$ticker"
    result=$(claude -p "$prompt

Return ONLY minified JSON: {\"catalyst\":\"<one sentence>\",\"headlines\":[\"<verbatim>\",\"<verbatim>\"]}
Use null for catalyst and [] for headlines if the page shows no recent news.

PAGE CONTENT:
$(head -c 12000 "$txt")" 2>/dev/null \
      | tr -d '\000' | grep -o '{.*}' | head -1)
    if [[ -n "$result" ]] && echo "$result" | jq -e '.catalyst? // .headlines?' >/dev/null 2>&1; then
      echo "$result" | jq -c '{catalyst: (.catalyst // null),
                               headlines: ((.headlines // []) | map(select(type=="string")) | .[0:2])}'
      return 0
    fi
    log "  $ticker: llm extraction failed, using scraped headlines"
  fi

  # Fallback: pull plausible headline lines straight off the page.
  local heads
  heads=$(grep -iE '[A-Za-z]{3,}' "$txt" \
          | grep -iE "$ticker|shares|stock|earnings|announce|report|upgrade|downgrade|FDA|acquisition|guidance" \
          | awk 'length($0) > 30 && length($0) < 180' | head -2 \
          | jq -R -s -c 'split("\n") | map(select(length > 0))' 2>/dev/null)
  [[ -n "$heads" ]] || heads='[]'
  echo "$heads" | jq -c '{catalyst: (.[0] // null), headlines: .}'
}

ENRICHED="$WORKDIR/enriched.json"
echo '[]' > "$ENRICHED"

if [[ "$COUNT" -gt 0 ]]; then
  while IFS=$'\t' read -r rank symbol price gap vol; do
    [[ -n "${symbol:-}" ]] || continue
    log "catalyst $rank/$COUNT: $symbol"
    cat_json=$(lookup_catalyst "$symbol") || cat_json='{"catalyst":null,"headlines":[]}'
    jq -c --argjson r "$rank" --arg s "$symbol" --argjson p "$price" \
          --argjson g "$gap" --argjson v "$vol" --argjson c "$cat_json" \
       '. + [{rank:$r, symbol:$s, price:$p, gap_pct:$g, premarket_volume:$v,
              catalyst:$c.catalyst, headlines:$c.headlines}]' \
       "$ENRICHED" > "$ENRICHED.tmp" && mv "$ENRICHED.tmp" "$ENRICHED"
  done < <(jq -r '.[] | [.rank, .symbol, .price, .gap_pct, .premarket_volume] | @tsv' "$FILTERED")
fi

# ---------------------------------------------------------------- stage 4: output
OUTFILE="$OUTDIR/premarket_gappers_$(date +%F).json"
jq -n --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --slurpfile g "$ENRICHED" \
  '{scanned_at: $ts, gappers: $g[0]}' > "$OUTFILE" || die "failed writing $OUTFILE"
log "wrote $OUTFILE"

N=$(jq '.gappers | length' "$OUTFILE")
TOP=$(jq -r '[.gappers[0:3][]
              | "\(.symbol) (\(.gap_pct | . * 10 | round / 10)%) — \(.catalyst // "no catalyst found")"]
             | join(", ")' "$OUTFILE")
if [[ -n "$TOP" ]]; then
  echo "Premarket Gappers: $N names. Top: $TOP"
else
  echo "Premarket Gappers: $N names."
fi
