"""Local visual fixture: no app vault, session token, game or Internet calls."""
from pathlib import Path
from flask import Flask, jsonify, request

ROOT = Path(__file__).resolve().parents[1]
app = Flask(__name__, static_folder=str(ROOT / "static"))


@app.get("/")
def index():
    return """<!doctype html><html data-theme="dark"><head><title>Career UI fixture</title>
    <meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/styles.css">
    </head><body><main style="max-width:1200px;margin:36px auto"><p style="margin:24px;color:#999">UI test fixture - synthetic account, no network or vault.</p><div id="root"></div></main>
    <script src="/static/vendor/react.production.min.js"></script><script src="/static/vendor/react-dom.production.min.js"></script>
    <script src="/static/vendor/babel.min.js"></script><script src="/static/data.js"></script>
    <script type="text/babel" src="/static/card-variants.jsx"></script><script type="text/babel" src="/static/career-live.jsx"></script>
    <script type="text/babel">const request=async(path,opts)=>{const r=await fetch(path,{...opts,headers:{'Content-Type':'application/json'}});return r.json();};
    function Fixture(){const [acct,setAcct]=React.useState({id:'fixture',in_game_name:'Synthetic connected account',rivals_uid:'123456789',rivals_platform:'pc',stats_provider:'netease',netease_connected:true,
      current_rank:'Diamond II',current_points:4350,peak_rank:'Celestial I',peak_points:5016,last_refresh_source:'netease',last_refresh_status:'ok',last_refresh_ts:Math.floor(Date.now()/1000)-40,
      netease_rank:{season:19,qualification:'estimated',placements_completed:0,upstream_updated_at:1787093892},
      netease_profile:{season:19,matches:0,wins:0,kills:0,deaths:0,assists:0}});
      const opts={hideDetails:true,hideCopy:true}; const noop=()=>{}; const doRefresh=()=>setAcct({...acct,last_refresh_ts:Math.floor(Date.now()/1000)});
      return <><div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(260px,1fr))',gap:16,margin:24}}>
        <window.CardRefined acct={acct} opts={opts} onOpen={noop} onPin={noop} onRefresh={doRefresh}/>
        <window.CardRefined acct={{...acct,id:'unconnected',in_game_name:'Synthetic unconnected account',netease_rank:null,netease_profile:null,netease_connected:false,last_refresh_status:'error',last_refresh_code:'career_not_connected'}} opts={opts} onOpen={noop} onPin={noop} onRefresh={noop}/>
        </div><window.NetEaseCareerPanel request={request} accounts={[acct]}/>
        <window.TableView accounts={[acct]} opts={opts} onOpen={noop} onPin={noop} onRefresh={doRefresh} refreshingIds={new Set()}/>
        <window.LadderView accounts={[acct]} opts={opts} onOpen={noop} onPin={noop} onRefresh={doRefresh} refreshingIds={new Set()} rankField='current_rank'/></>;
    }
    ReactDOM.createRoot(document.getElementById('root')).render(<Fixture/>);</script></body></html>"""


@app.get("/api/career/status")
def status():
    return jsonify(configured=True, uid="123456789", platform="pc", season=17, seasons=[19, 17], expires_at=1789405200, rank_available=True, rank_season=19)


@app.post("/api/career/refresh")
def refresh():
    season = (request.get_json() or {}).get("season", 17)
    return jsonify(uid="123456789", platform="pc", season=season, source="netease", fetched_at=1788801600,
                   ranks={"current_season": 19, "current": {"rank": "Diamond II", "points": 4350, "qualification": "estimated", "placements_completed": 0},
                          "peak": {"peak_rank": "Celestial I", "peak_points": 5016, "season": 17}},
                   profile={"matches": 47, "wins": 20, "kills": 661, "deaths": 293, "assists": 828} if season == 17 else dict.fromkeys(["matches", "wins", "kills", "deaths", "assists"], 0),
                   recent_matches=[{"played_at": 1782187019 - n*900, "season": 17, "mode_id": 2, "result": "win" if n % 2 == 0 else "loss",
                                    "kills": 36, "deaths": 6, "assists": 7, "duration_seconds": 818} for n in range(50)])


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=55031, debug=False)
