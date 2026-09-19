from flask import Flask, jsonify
import os,time
app=Flask(__name__,static_folder="static",static_url_path="")
start=float(os.getenv("STARTING_BALANCE","100"))
@app.get("/")
def home(): return app.send_static_file("index.html")
@app.get("/api/status")
def status(): return jsonify(mode="PAPER",equity=start,cash=start,pnl=0,daily_pnl=0,risk="NORMAL",positions=[])
@app.get("/api/decisions")
def decisions(): return jsonify([{"symbol":"SYSTEM","action":"WAIT","confidence":100,"reason":"Paper engine initialized; waiting for validated market data."}])
if __name__=="__main__": app.run(host="0.0.0.0",port=8787)
