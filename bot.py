import os,asyncio,logging
from datetime import datetime,timezone
import aiohttp
from telegram import Bot

logging.basicConfig(level=logging.INFO)
log=logging.getLogger(__name__)

TOKEN="8618470619:AAEA94YIgZJZlDaDDGgFNIohxpd6Kl6YMxY"
CHAT="8493385467"
KEY="4e51890e2987488ca88a799c8bd6b1f1"
INTERVAL=600
RR=2.0
SIG=""

# Performance tracking
STATS={
    "wins":0,"losses":0,"consecutive_losses":0,
    "signals_today":0,"day":None,"paused":False,
    "active_signal":None
}

def mean(d):
    return sum(d)/len(d) if d else 0

def ema(p,n):
    if len(p)<n:
        return p[-1] if p else 0
    k=2/(n+1)
    r=mean(p[:n])
    for x in p[n:]:
        r=x*k+r*(1-k)
    return r

def atr(h,l,c,n=14):
    t=[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,len(c))]
    return mean(t[-n:]) if t else 0

def rsi(c,n=14):
    if len(c)<n+1:
        return 50
    g=[max(c[i]-c[i-1],0) for i in range(1,len(c))]
    ls=[max(c[i-1]-c[i],0) for i in range(1,len(c))]
    ag=mean(g[-n:])
    al=mean(ls[-n:])
    if al==0:
        return 100
    return 100-(100/(1+ag/al))

def wickratio(o,c,h,l,bull):
    rng=h-l
    if rng==0:
        return 0
    if bull:
        return (o-l)/rng
    if c<o:
        return (h-o)/rng
    return (h-c)/rng

def killzone():
    h=datetime.now(timezone.utc).hour
    if 7<=h<10:
        return True,"London Open Killzone"
    if 10<=h<13:
        return True,"London Midday"
    if 13<=h<16:
        return True,"NY Open Killzone"
    if 16<=h<21:
        return True,"NY Afternoon"
    return False,"Off-Hours"

def reset_daily():
    today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if STATS["day"]!=today:
        STATS["day"]=today
        STATS["signals_today"]=0
        STATS["paused"]=False
        log.info("Daily stats reset")

async def fetch(s,iv,n=100):
    url=f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval={iv}&outputsize={n}&format=JSON&apikey={KEY}"
    async with s.get(url,timeout=aiohttp.ClientTimeout(total=20)) as r:
        d=await r.json()
    if "values" not in d:
        raise RuntimeError(f"{iv}:{d.get('message','err')}")
    rows=sorted(d["values"],key=lambda x:x["datetime"])
    o=[float(x["open"]) for x in rows]
    h=[float(x["high"]) for x in rows]
    l=[float(x["low"]) for x in rows]
    c=[float(x["close"]) for x in rows]
    return o,h,l,c

async def fetchall(s):
    m5=await fetch(s,"5min",100)
    await asyncio.sleep(1)
    m15=await fetch(s,"15min",100)
    await asyncio.sleep(1)
    h1=await fetch(s,"1h",80)
    return m5,m15,h1

def htf_bias(h1):
    _,_,_,c=h1
    e200=ema(c,min(200,len(c)-1))
    price=c[-1]
    if price>e200*1.0008:
        return "bull"
    if price<e200*0.9992:
        return "bear"
    return "neutral"

def detect_bos(m15):
    o,h,l,c=m15
    sh=max(h[-22:-2])
    sl=min(l[-22:-2])
    bull=c[-1]>sh and c[-2]<=sh
    bear=c[-1]<sl and c[-2]>=sl
    return {"bullish":bull,"bearish":bear,"sh":sh,"sl":sl}

def detect_bos_retest(m15,bos,bias):
    o,h,l,c=m15
    price=c[-1]
    zone=(bos["sh"]-bos["sl"])*0.1
    if bias=="bull":
        return price>=bos["sh"]-zone and price<=bos["sh"]+zone*2
    if bias=="bear":
        return price<=bos["sl"]+zone and price>=bos["sl"]-zone*2
    return False

def detect_choch(c,h,l,bias,lookback=15):
    """
    Change of Character — first sign of reversal INSIDE a trend.
    Bull CHoCH: in a downtrend, price makes a higher low then breaks above
    the last lower high. Bear CHoCH: in an uptrend, price makes a lower high
    then breaks below the last higher low.
    """
    if bias=="bull":
        # Look for first higher low followed by break above recent lower high
        recent_lows=[l[-(i)] for i in range(2,lookback)]
        if len(recent_lows)<3:
            return False
        # Higher low formed if last low > second last low
        higher_low=l[-2]>l[-3]
        # Break above recent swing high
        recent_high=max(h[-lookback:-2])
        break_above=c[-1]>recent_high
        return higher_low and break_above
    if bias=="bear":
        recent_highs=[h[-(i)] for i in range(2,lookback)]
        if len(recent_highs)<3:
            return False
        lower_high=h[-2]<h[-3]
        recent_low=min(l[-lookback:-2])
        break_below=c[-1]<recent_low
        return lower_high and break_below
    return False

def detect_displacement(h,l,c,av):
    """
    Displacement filter — checks if the most recent impulsive move
    was strong enough (large body candle, above average ATR).
    Filters out weak, indecisive BOS candles.
    """
    if av==0:
        return False
    # Check last 3 candles for a displacement candle
    for i in range(1,4):
        body=abs(c[-i]-h[-i]) if c[-i]<h[-i] else abs(c[-i]-l[-i])
        candle_range=h[-i]-l[-i]
        if candle_range==0:
            continue
        body_pct=abs(c[-i]-(h[-i] if c[-i]<h[-i] else l[-i]))/candle_range
        # Strong displacement: range > 1.5x ATR and body > 60% of range
        if candle_range>av*1.5 and body_pct>0.6:
            return True
    return False

def detect_liquidity_grab(h,l,c):
    eqhigh=max(h[-30:-5])
    eqlow=min(l[-30:-5])
    last_h=h[-2]
    last_l=l[-2]
    last_c=c[-2]
    bull_grab=False
    bear_grab=False
    if last_l<eqlow and last_c>eqlow:
        wick=last_c-last_l
        body=abs(last_c-h[-2])
        if wick>body*1.1:
            bull_grab=True
    if last_h>eqhigh and last_c<eqhigh:
        wick=last_h-last_c
        body=abs(last_c-l[-2])
        if wick>body*1.1:
            bear_grab=True
    return bull_grab,bear_grab,eqhigh,eqlow

def detect_orderblock(o,h,l,c,bias):
    if bias=="bull":
        for i in range(3,min(40,len(c)-2)):
            if c[-i]<o[-i]:
                ok=all(c[-(i-j)]>o[-(i-j)] for j in range(1,3))
                if ok:
                    return {"upper":h[-i],"lower":l[-i]}
    if bias=="bear":
        for i in range(3,min(40,len(c)-2)):
            if c[-i]>o[-i]:
                ok=all(c[-(i-j)]<o[-(i-j)] for j in range(1,3))
                if ok:
                    return {"upper":h[-i],"lower":l[-i]}
    return None

def detect_fvg(h,l,bias):
    for i in range(2,min(20,len(h)-1)):
        if bias=="bull":
            if l[-(i-1)]>h[-(i+1)] and (l[-(i-1)]-h[-(i+1)])>0.3:
                return {"upper":l[-(i-1)],"lower":h[-(i+1)]}
        if bias=="bear":
            if h[-(i-1)]<l[-(i+1)] and (l[-(i+1)]-h[-(i-1)])>0.3:
                return {"upper":l[-(i+1)],"lower":h[-(i-1)]}
    return None

def fib_zone(sh,sl,bias):
    rng=sh-sl
    if bias=="bull":
        lo=sh-rng*0.786
        hi=sh-rng*0.618
    else:
        lo=sl+rng*0.618
        hi=sl+rng*0.786
    buf=(hi-lo)*0.25
    return lo-buf,hi+buf

def get_signal(m5,m15,h1):
    o5,h5,l5,c5=m5
    o15,h15,l15,c15=m15

    bias=htf_bias(h1)
    ok,sess=killzone()
    if not ok or bias=="neutral":
        return None

    price=c5[-1]
    av=atr(h5,l5,c5)
    rv=rsi(c15)
    e21=ema(c15,21)
    e50=ema(c15,50)

    bos=detect_bos(m15)
    bos_retest=detect_bos_retest(m15,bos,bias)
    choch=detect_choch(c5,h5,l5,bias)
    displaced=detect_displacement(h5,l5,c5,av)
    bull_grab,bear_grab,eqh,eql=detect_liquidity_grab(h5,l5,c5)
    ob=detect_orderblock(o15,h15,l15,c15,bias)
    fvg=detect_fvg(h5,l5,bias)
    flo,fhi=fib_zone(bos["sh"],bos["sl"],bias)
    in_fib=flo<=price<=fhi
    in_ob=ob and ob["lower"]<=price<=ob["upper"]
    in_fvg=fvg and fvg["lower"]<=price<=fvg["upper"]
    wb=wickratio(o5[-2],c5[-2],h5[-2],l5[-2],True)>0.5
    wbr=wickratio(o5[-2],c5[-2],h5[-2],l5[-2],False)>0.5

    if bias=="bull":
        ema_ok=e21>e50 and price>e21
        rsi_ok=40<rv<72
        fakeout_safe=bull_grab or bos["bullish"]
        factors=[
            ("M15 Break of Structure",bos["bullish"]),
            ("BOS Retest",bos_retest),
            ("CHoCH (Change of Character)",choch),
            ("Displacement (Strong Move)",displaced),
            ("Fibonacci 61.8-78.6%",in_fib),
            ("Fair Value Gap",bool(in_fvg)),
            ("RSI Momentum "+str(round(rv,1)),rsi_ok),
            ("Liquidity Grab",bull_grab),
            ("Order Block",bool(in_ob)),
            ("Wick Rejection",wb),
        ]
        score=sum(1 for _,hit in factors if hit)
        if score>=6 and ema_ok and fakeout_safe and in_fib:
            sl=min(price-av*1.3,eql-av*0.3,bos["sl"]-av*0.2)
            tp=price+(price-sl)*RR
            be=price+(price-sl)*1.0
            return {"d":"BUY","p":price,"sl":sl,"tp":tp,"be":be,
                    "sess":sess,"score":score,"factors":factors,
                    "av":av,"bos":bos["sh"],"eql":eql,"eqh":eqh}

    if bias=="bear":
        ema_ok=e21<e50 and price<e21
        rsi_ok=28<rv<60
        fakeout_safe=bear_grab or bos["bearish"]
        factors=[
            ("M15 Break of Structure",bos["bearish"]),
            ("BOS Retest",bos_retest),
            ("CHoCH (Change of Character)",choch),
            ("Displacement (Strong Move)",displaced),
            ("Fibonacci 61.8-78.6%",in_fib),
            ("Fair Value Gap",bool(in_fvg)),
            ("RSI Momentum "+str(round(rv,1)),rsi_ok),
            ("Liquidity Grab",bear_grab),
            ("Order Block",bool(in_ob)),
            ("Wick Rejection",wbr),
        ]
        score=sum(1 for _,hit in factors if hit)
        if score>=6 and ema_ok and fakeout_safe and in_fib:
            sl=max(price+av*1.3,eqh+av*0.3,bos["sl"]+av*0.2)
            tp=price-(sl-price)*RR
            be=price-(sl-price)*1.0
            return {"d":"SELL","p":price,"sl":sl,"tp":tp,"be":be,
                    "sess":sess,"score":score,"factors":factors,
                    "av":av,"bos":bos["sl"],"eql":eql,"eqh":eqh}
    return None

def buildmsg(s):
    dist=abs(s["p"]-s["sl"])
    now=datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    chart="https://www.tradingview.com/chart/?symbol=OANDA:XAUUSD&interval=5"
    lines=[]
    for name,hit in s["factors"]:
        prefix="[x]" if hit else "[ ]"
        lines.append(prefix+" "+name)
    conf_text="\n".join(lines)
    total=STATS["wins"]+STATS["losses"]
    wr=round(STATS["wins"]/total*100,1) if total>0 else 0
    return (
        f"XAUUSD {s['d']} SIGNAL\n"
        f"ICT Smart Money v3\n"
        f"------------------------\n"
        f"{now}\n"
        f"{s['sess']}\n\n"
        f"ENTRY: {s['p']:.2f}\n"
        f"STOP LOSS: {s['sl']:.2f} ({dist:.1f} pts)\n"
        f"TAKE PROFIT: {s['tp']:.2f}\n"
        f"BREAKEVEN AT: {s['be']:.2f} (move SL to entry)\n"
        f"Risk:Reward: 1:{RR}\n"
        f"------------------------\n"
        f"CONFLUENCE ({s['score']}/10)\n"
        f"{conf_text}\n"
        f"------------------------\n"
        f"BOS Level: {s['bos']:.2f}\n"
        f"Liquidity: {s['eql']:.2f} / {s['eqh']:.2f}\n"
        f"ATR: {s['av']:.2f}\n"
        f"------------------------\n"
        f"Today: {STATS['signals_today']} signals\n"
        f"Record: {STATS['wins']}W / {STATS['losses']}L ({wr}%)\n"
        f"Reply WIN or LOSS after trade closes.\n"
        f"------------------------\n"
        f"Chart: {chart}\n"
        f"Only risk 1-2% per trade. Respect the SL."
    )

def daily_summary():
    total=STATS["wins"]+STATS["losses"]
    wr=round(STATS["wins"]/total*100,1) if total>0 else 0
    now=datetime.now(timezone.utc).strftime("%d %b %Y")
    return (
        f"DAILY SUMMARY - {now}\n"
        f"------------------------\n"
        f"Signals sent: {STATS['signals_today']}\n"
        f"Wins: {STATS['wins']}\n"
        f"Losses: {STATS['losses']}\n"
        f"Win Rate: {wr}%\n"
        f"Consecutive losses: {STATS['consecutive_losses']}\n"
        f"------------------------\n"
        f"Reply WIN or LOSS after any open trades close.\n"
        f"New day starts fresh tomorrow."
    )

async def check_breakeven(bot,price):
    """Alert user to move SL to breakeven when price reaches +1R"""
    sig=STATS["active_signal"]
    if sig is None:
        return
    if sig["d"]=="BUY" and price>=sig["be"] and not sig.get("be_alerted"):
        await bot.send_message(chat_id=CHAT,text=(
            f"BREAKEVEN ALERT\n"
            f"Price reached {price:.2f}\n"
            f"Move your Stop Loss to entry: {sig['p']:.2f}\n"
            f"Lock in a risk-free trade!"
        ))
        STATS["active_signal"]["be_alerted"]=True
    elif sig["d"]=="SELL" and price<=sig["be"] and not sig.get("be_alerted"):
        await bot.send_message(chat_id=CHAT,text=(
            f"BREAKEVEN ALERT\n"
            f"Price reached {price:.2f}\n"
            f"Move your Stop Loss to entry: {sig['p']:.2f}\n"
            f"Lock in a risk-free trade!"
        ))
        STATS["active_signal"]["be_alerted"]=True

async def handle_wl(bot,text,price):
    """Handle WIN/LOSS/STATS replies"""
    text=text.strip().upper()
    if text=="WIN":
        STATS["wins"]+=1
        STATS["consecutive_losses"]=0
        STATS["active_signal"]=None
        total=STATS["wins"]+STATS["losses"]
        wr=round(STATS["wins"]/total*100,1)
        await bot.send_message(chat_id=CHAT,text=(
            f"WIN logged!\n"
            f"Record: {STATS['wins']}W / {STATS['losses']}L ({wr}%)\n"
            f"Consecutive losses reset to 0."
        ))
    elif text=="LOSS":
        STATS["losses"]+=1
        STATS["consecutive_losses"]+=1
        STATS["active_signal"]=None
        total=STATS["wins"]+STATS["losses"]
        wr=round(STATS["wins"]/total*100,1)
        msg=(
            f"LOSS logged.\n"
            f"Record: {STATS['wins']}W / {STATS['losses']}L ({wr}%)\n"
            f"Consecutive losses: {STATS['consecutive_losses']}"
        )
        if STATS["consecutive_losses"]>=3:
            STATS["paused"]=True
            msg+=f"\n\nBOT PAUSED after 3 consecutive losses.\nTake a break and review the market.\nSend RESUME when ready."
        await bot.send_message(chat_id=CHAT,text=msg)
    elif text=="STATS":
        total=STATS["wins"]+STATS["losses"]
        wr=round(STATS["wins"]/total*100,1) if total>0 else 0
        await bot.send_message(chat_id=CHAT,text=(
            f"PERFORMANCE STATS\n"
            f"------------------------\n"
            f"Total trades: {total}\n"
            f"Wins: {STATS['wins']}\n"
            f"Losses: {STATS['losses']}\n"
            f"Win Rate: {wr}%\n"
            f"Signals today: {STATS['signals_today']}\n"
            f"Consecutive losses: {STATS['consecutive_losses']}\n"
            f"Bot paused: {'Yes' if STATS['paused'] else 'No'}"
        ))
    elif text=="RESUME":
        STATS["paused"]=False
        STATS["consecutive_losses"]=0
        await bot.send_message(chat_id=CHAT,text="Bot RESUMED. Hunting setups...")

async def run():
    global SIG
    bot=Bot(token=TOKEN)
    await bot.send_message(chat_id=CHAT,text=(
        "XAUUSD ICT Bot v3 ONLINE\n\n"
        "New features:\n"
        "+ CHoCH detection\n"
        "+ Displacement filter\n"
        "+ Breakeven alerts at +1R\n"
        "+ Consecutive loss protection (pauses after 3)\n"
        "+ Daily statistics\n"
        "+ WIN/LOSS tracking\n\n"
        "Commands:\n"
        "WIN - log a winning trade\n"
        "LOSS - log a losing trade\n"
        "STATS - see your performance\n"
        "RESUME - resume after pause\n\n"
        "Sessions: London + New York (9am-11pm SA)\n"
        "Scanning every 10 minutes...\n\n"
        "Hunting setups..."
    ))
    log.info("Bot v3 online")

    last_summary_day=None

    async with aiohttp.ClientSession() as s:
        while True:
            try:
                reset_daily()

                # Send daily summary at end of session (9pm UTC = 11pm SA)
                now_h=datetime.now(timezone.utc).hour
                today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if now_h==21 and last_summary_day!=today:
                    await bot.send_message(chat_id=CHAT,text=daily_summary())
                    last_summary_day=today

                # Skip scanning if bot is paused
                if STATS["paused"]:
                    log.info("Bot paused - skipping scan")
                    await asyncio.sleep(INTERVAL)
                    continue

                # Skip API calls during off-hours to save credits
                ok,sess=killzone()
                if not ok:
                    log.info(f"Off-hours - sleeping. Next check in {INTERVAL//60} min")
                    await asyncio.sleep(INTERVAL)
                    continue

                m5,m15,h1=await fetchall(s)
                price=m5[3][-1]
                bias=htf_bias(h1)
                log.info(f"Price:{price:.2f} Bias:{bias} Session:{sess}")

                # Check breakeven on active signal
                await check_breakeven(bot,price)

                sig=get_signal(m5,m15,h1)
                if sig:
                    k=f"{sig['d']}_{int(price)}"
                    if k!=SIG:
                        await bot.send_message(chat_id=CHAT,text=buildmsg(sig))
                        SIG=k
                        STATS["signals_today"]+=1
                        STATS["active_signal"]=sig
                        STATS["active_signal"]["be_alerted"]=False
                        log.info(f"Signal:{sig['d']}@{price:.2f} Score:{sig['score']}/10")
                else:
                    log.info(f"No signal. Price:{price:.2f} Bias:{bias}")

            except Exception as e:
                log.error(f"Err:{e}")
                try:
                    await bot.send_message(chat_id=CHAT,text=f"Error: {str(e)[:100]}")
                except:
                    pass
            await asyncio.sleep(INTERVAL)

asyncio.run(run())
