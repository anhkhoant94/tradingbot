const DAY = 24 * 60 * 60;
const MAX_SYMBOLS = 40;

function ictStamp(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Ho_Chi_Minh",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(date).reduce((out, p) => {
    out[p.type] = p.value;
    return out;
  }, {});
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}

function cleanSymbol(value) {
  return String(value || "").trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
}

async function fetchHistory(symbol) {
  const now = Math.floor(Date.now() / 1000);
  const from = now - 45 * DAY;
  const url = `https://histdatafeed.vps.com.vn/tradingview/history?symbol=${encodeURIComponent(symbol)}&resolution=D&from=${from}&to=${now + DAY}`;
  const response = await fetch(url, {
    headers: { "User-Agent": "ez-trading-live-status/1.0" },
  });
  if (!response.ok) {
    throw new Error(`HTTP_${response.status}`);
  }
  const payload = await response.json();
  if (payload.s !== "ok" || !Array.isArray(payload.t) || !payload.t.length || !Array.isArray(payload.c)) {
    throw new Error("NO_DATA");
  }
  const i = payload.t.length - 1;
  const rawClose = Number(payload.c[i]);
  const rawOpen = Number(payload.o?.[i]);
  const rawHigh = Number(payload.h?.[i]);
  const rawLow = Number(payload.l?.[i]);
  const stockLike = symbol !== "VNINDEX";
  const scale = stockLike && rawClose > 1000 ? 1000 : 1;
  const toPrice = (v) => Number.isFinite(v) && v > 0 ? Number((v / scale).toFixed(4)) : null;
  return {
    ok: true,
    date: new Date(Number(payload.t[i]) * 1000).toISOString().slice(0, 10),
    close: toPrice(rawClose),
    open: toPrice(rawOpen),
    high: toPrice(rawHigh),
    low: toPrice(rawLow),
  };
}

module.exports = async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Cache-Control", "s-maxage=300, stale-while-revalidate=60");
  const rawSymbols = String(req.query.symbols || "MSB")
    .split(",")
    .map(cleanSymbol)
    .filter(Boolean)
    .filter((s, i, arr) => arr.indexOf(s) === i)
    .slice(0, MAX_SYMBOLS);
  const symbols = rawSymbols.length ? rawSymbols : ["MSB"];
  const now = new Date();

  const quotes = {};
  await Promise.all(symbols.map(async (symbol) => {
    try {
      quotes[symbol] = await fetchHistory(symbol);
    } catch (err) {
      quotes[symbol] = { ok: false, reason: String(err && err.message || err) };
    }
  }));

  let vnindex;
  try {
    const vni = await fetchHistory("VNINDEX");
    vnindex = {
      symbol: "VNINDEX",
      ok: true,
      latest: vni.date,
      latestClose: vni.close,
    };
  } catch (err) {
    vnindex = { symbol: "VNINDEX", ok: false, reason: String(err && err.message || err) };
  }

  const dates = Object.values(quotes)
    .filter((q) => q && q.ok && q.date)
    .map((q) => q.date)
    .concat(vnindex && vnindex.ok && vnindex.latest ? [vnindex.latest] : []);

  res.status(200).json({
    source: "vercel_edge_live_status",
    updatedAtUtc: now.toISOString().replace(/\.\d{3}Z$/, "Z"),
    updatedAtICT: ictStamp(now),
    latestPriceDate: dates.length ? dates.sort().slice(-1)[0] : null,
    symbols,
    quotes,
    vnindex,
  });
};
