# -*- coding: utf-8 -*-
import json
import os

CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "eastmoney_board_rank",
    "_hist_cache",
)


def write_init(kind: str) -> str:
    codes_path = os.path.join(CACHE, f"_codes_{kind}.json")
    codes = json.load(open(codes_path, encoding="utf-8"))
    js = (
        f'window.__EM_KIND="{kind}";'
        'window.__EM_BEG="20260601";'
        'window.__EM_END="20260805";'
        f"window.__EM_CODES={json.dumps(codes)};"
        "window.__EM_IDX=0;window.__EM_OK=0;window.__EM_FAIL=0;"
        "window.__emLoadOne=function(code){"
        "  var url='https://91.push2his.eastmoney.com/api/qt/stock/kline/get?secid=90.'+code"
        "    +'&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'"
        "    +'&klt=101&fqt=0&beg='+window.__EM_BEG+'&end='+window.__EM_END"
        "    +'&smplmt=10000&lmt=1000000&cb=emHistCb_'+code;"
        "  return new Promise(function(resolve){"
        "    var t=setTimeout(function(){resolve({code:code,ok:false,err:'timeout',klines:[]});},20000);"
        "    window['emHistCb_'+code]=function(data){"
        "      clearTimeout(t);"
        "      var kl=((data&&data.data&&data.data.klines)||[]);"
        "      resolve({code:code,ok:true,n:kl.length,klines:kl});"
        "    };"
        "    var s=document.createElement('script');"
        "    s.src=url;"
        "    s.onerror=function(){clearTimeout(t);resolve({code:code,ok:false,err:'script error',klines:[]});};"
        "    document.head.appendChild(s);"
        "  });"
        "};"
        "window.__emRun=async function(batchSize){"
        "  batchSize=batchSize||40;"
        "  var items=[];"
        "  var start=window.__EM_IDX;"
        "  var end=Math.min(window.__EM_CODES.length, start+batchSize);"
        "  for(var i=start;i<end;i++){"
        "    var code=window.__EM_CODES[i];"
        "    var r=await window.__emLoadOne(code);"
        "    if(r.ok){window.__EM_OK++;} else {window.__EM_FAIL++;}"
        "    items.push(r);"
        "    await new Promise(function(res){setTimeout(res,80);});"
        "  }"
        "  window.__EM_IDX=end;"
        "  return {"
        "    kind:window.__EM_KIND,"
        "    idx:window.__EM_IDX,"
        "    total:window.__EM_CODES.length,"
        "    ok:window.__EM_OK,"
        "    fail:window.__EM_FAIL,"
        "    done:window.__EM_IDX>=window.__EM_CODES.length,"
        "    items:items"
        "  };"
        "};"
        "'inited:'+window.__EM_CODES.length;"
    )
    out = os.path.join(CACHE, f"_init_{kind}.js")
    with open(out, "w", encoding="utf-8") as f:
        f.write(js)
    print(out, "codes", len(codes), "bytes", os.path.getsize(out))
    return out


if __name__ == "__main__":
    write_init("industry")
    write_init("concept")
