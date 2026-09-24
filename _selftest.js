// 把 index.html 里的脚本抽出来，在 Node 里配上最小 DOM 桩跑一遍所有视图，
// 用来验证渲染路径不会抛异常、且能产出内容。
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");

/* 凭据回归护栏：这个项目是要发布出去的，源码里绝不允许出现任何凭据或内网地址。
   （上一个项目就是因为把 NAS 账号密码硬编码进 app.js 并推上公开仓库才泄露的。） */
const CRED = /REDACTED-BY-HISTORY-REWRITE|192\.168\.\d+\.\d+|WEBDAV_AUTH\s*=\s*['"][^'"]+['"]/;
if (CRED.test(html)) {
  console.error("❌ index.html 里出现了凭据或内网地址，拒绝通过：", html.match(CRED)[0]);
  process.exit(1);
}

const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error("找不到 <script>"); process.exit(1); }
const js = m[1];

/* ── 最小 DOM / 浏览器桩 ── */
const store = {};
const els = {};
let loc = { origin: "https://film.example.com", pathname: "/", search: "", hash: "" };

function el(sel) {
  // 带空格的复合选择器按真实浏览器的行为返回 null
  if (sel.indexOf(" ") >= 0) return null;
  if (!els[sel]) els[sel] = { innerHTML: "", value: "", textContent: "",
                              style: {}, offsetLeft: 0,
                              classList: { add(){}, remove(){} } };
  return els[sel];
}

global.localStorage = {
  getItem: k => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: k => { delete store[k]; }
};
global.location = loc;
global.history = {
  replaceState(_a, _b, url) {
    const i = String(url).indexOf("#");
    loc.hash = i >= 0 ? String(url).slice(i) : "";
  }
};
global.document = {
  querySelector: el,
  querySelectorAll: () => [],
  createElement: () => ({ click(){}, style:{}, href:"", download:"" }),
  body: { appendChild(){}, removeChild(){} }
};
global.window = { addEventListener(){}, scrollTo(){}, scrollY: 0,
                  matchMedia: () => ({ matches:false }) };
global.navigator = {};
global.confirm = () => true;
global.clearTimeout = () => {};
global.setTimeout = () => 0;

/* ── 注入测试驱动 ── */
const driver = `
save(demoData());

const CASES = [
  { name:"机库首页",    search:"",       hash:"#/",
    must:["Leica M6","尼康 FM2","奥林巴斯 XA2","禄来 3.5F","214 天","示例数据","24/36 张"],
    mustNot:[] },
  { name:"碰 m6 打开",  search:"?c=m6",  hash:"",
    must:["Kodak Portra 400","EI 1600","推 2 档","92<small>天</small>",
          "还剩 12 张","过片 ＋1","135 · 36 张"],
    mustNot:["该冲了"] },
  { name:"120 机身",    search:"",       hash:"#/c/rollei",
    must:["禄来 3.5F","120 · 12 张","还剩 7 张","Ilford Delta 100"],
    mustNot:[] },
  { name:"拍满的卷",    search:"",       hash:"#/c/xa2",
    must:["Kodak ColorPlus 200","214 天","该冲了","偏久"],
    mustNot:[] },
  { name:"未登记新 ID",  search:"",       hash:"#/c/brand-new",
    must:["新挂件","机身名称","画幅","brand-new"],
    mustNot:[] },
  { name:"换卷表单",    search:"",       hash:"#/c/m6/load",
    must:["换一卷","Kodak Portra 400","Ilford HP5 Plus 400","记进历史","总共多少张"],
    mustNot:[] },
  { name:"编辑当前卷",  search:"",       hash:"#/c/m6/edit",
    must:["编辑这卷",'value="1600"',"推到 1600 拍夜景"],
    mustNot:[] },
  { name:"编辑机身",    search:"",       hash:"#/c/m6/setup",
    must:["编辑机身",'value="Leica"','value="M6"',"135","画幅"],
    mustNot:["新挂件"] },
  { name:"历史记录",    search:"",       hash:"#/c/m6/history",
    must:["Ilford HP5 Plus 400","Fujifilm C200","22 天","120 天"],
    mustNot:[] },
  { name:"统计页",      search:"",       hash:"#/stats",
    must:["统计","机身总数","当前有卷","已用胶卷","累计快门","188","240",
          "常用胶卷 Top","Ilford HP5 Plus 400","机身状态"],
    mustNot:[] },
  { name:"设置与备份",  search:"",       hash:"#/settings",
    must:["导出备份","已登记的机身 ID","?c=","清空全部数据",
          "同步到自己的 NAS","WebDAV 目录地址","保存同步设置"],
    mustNot:["REDACTED-BY-HISTORY-REWRITE","REDACTED-BY-HISTORY-REWRITE"] }
];

const REPORT = [];
let pass = 0, fail = 0;

function check(name, ok, note) {
  REPORT.push([name, ok ? "通过" : "不对", note || ""]);
  ok ? pass++ : fail++;
}

for (const cs of CASES) {
  __setLoc(cs.search, cs.hash);
  let out;
  try { render(); out = __app(); }
  catch (e) { REPORT.push([cs.name, "抛异常 " + e.message, ""]); fail++; continue; }

  const miss = cs.must.filter(s => out.indexOf(s) < 0);
  const bad  = cs.mustNot.filter(s => out.indexOf(s) >= 0);
  check(cs.name, !miss.length && !bad.length,
        (miss.length ? "缺「" + miss.join("、") + "」 " : "") +
        (bad.length  ? "不该出现「" + bad.join("、") + "」" : "") ||
        (cs.must.length + " 项断言 / " + out.length + " 字符"));
}

/* ── 过片计数 ── */
try {
  __setLoc("", "#/c/m6"); render();
  const before = __app().indexOf("还剩 12 张") >= 0;
  advanceShot("m6");
  const after  = __app().indexOf("还剩 11 张") >= 0 && __app().indexOf("<b>25</b> / 36 张") >= 0;
  const stored = load().cameras.m6.loaded.shots === 25;
  check("过片 ＋1", before && after && stored,
        "前=" + before + " 后=" + after + " 落库=" + stored);
} catch (e) { check("过片 ＋1", false, "抛异常 " + e.message); }

/* ── 拍满后封顶 ── */
try {
  const db = load();
  db.cameras.m6.loaded.shots = 35;
  save(db);
  __setLoc("", "#/c/m6"); render();
  advanceShot("m6");
  const ui = __app();
  const showsFull = ui.indexOf("这卷已经拍满") >= 0 && ui.indexOf("这卷拍满了，可以回卷") >= 0;
  advanceShot("m6");                                   // 再点一次不应越界
  const capped = load().cameras.m6.loaded.shots === 36;
  check("拍满封顶", showsFull && capped, "提示=" + showsFull + " 不越界=" + capped);
} catch (e) { check("拍满封顶", false, "抛异常 " + e.message); }

/* ── 装卷归档流程 ── */
try {
  __setLoc("", "#/c/fm2/load");
  render();
  __setValue("#f-stock", "Adox CMS 20 II");
  __setValue("#f-iso", "20");
  __setValue("#f-ei", "");
  __setValue("#f-shots", "36");
  __setValue("#f-note", "超细颗粒");
  saveLoad("fm2", false);
  const fm2 = load().cameras.fm2;
  const ok1 = fm2.loaded && fm2.loaded.stock === "Adox CMS 20 II";
  const ok2 = fm2.history.length === 2 &&
              fm2.history[fm2.history.length - 1].stock === "Ilford HP5 Plus 400";
  const ok3 = !!fm2.history[fm2.history.length - 1].unloadedAt;
  const ok4 = fm2.loaded.total === 36;
  check("装卷归档流程", ok1 && ok2 && ok3 && ok4,
        "新卷=" + ok1 + " 旧卷入史=" + ok2 + " 卸卷日期=" + ok3 + " 张数=" + ok4);
} catch (e) { check("装卷归档流程", false, "抛异常 " + e.message); }

/* ── 已拍张数不得超过总张数 ── */
try {
  __setLoc("", "#/c/fm2/edit"); render();
  __setValue("#f-stock", "Kodak Portra 400");
  __setValue("#f-shots", "99");
  __setValue("#f-total", "36");
  saveLoad("fm2", true);
  const kept = load().cameras.fm2.loaded.shots === 36;   // 上一轮装卷时是 36，不该被 99 覆盖
  check("张数越界被拒", kept, "当前 shots=" + load().cameras.fm2.loaded.shots);
} catch (e) { check("张数越界被拒", false, "抛异常 " + e.message); }

/* ── 同步设置：只落本机、不进源码 ── */
try {
  __setLoc("", "#/settings"); render();
  __setValue("#dav-url", "https://nas.example.com/dav/film/");
  __setValue("#dav-user", "filmapp");
  __setValue("#dav-pass", "app-password");
  saveDavFromForm();
  const c = getDav();
  const ok1 = c && c.url === "https://nas.example.com/dav/film/" && c.user === "filmapp";
  const ok2 = __app().indexOf("立即同步") >= 0 && __app().indexOf("从 NAS 恢复") >= 0;

  // 密码留空应沿用已有的，不被清掉
  __setValue("#dav-pass", "");
  saveDavFromForm();
  const ok3 = getDav().pass === "app-password";

  clearDav();
  const ok4 = getDav() === null;
  check("同步设置本机存储", ok1 && ok2 && ok3 && ok4,
        "落库=" + ok1 + " 按钮=" + ok2 + " 留空保留=" + ok3 + " 可清除=" + ok4);
} catch (e) { check("同步设置本机存储", false, "抛异常 " + e.message); }

/* ── 标签 URL 生成 ── */
__setLoc("", "#/settings");
render();
const url = tagUrl("m6");
const okUrl = url.indexOf("?c=m6") > 0 && url.indexOf("https://") === 0;
check("标签 URL 生成", okUrl, url);

__report(REPORT, pass, fail);
`;

global.__setLoc = (search, hash) => { loc.search = search; loc.hash = hash; };
global.__app = () => el("#app").innerHTML;
global.__setValue = (sel, v) => { el(sel).value = v; };
global.__report = (rows, pass, fail) => {
  console.log("检查项".padEnd(20) + "结果".padEnd(10) + "备注");
  console.log("-".repeat(88));
  for (const [name, st, note] of rows) console.log(name.padEnd(20) + st.padEnd(12) + note);
  console.log("-".repeat(88));
  console.log(`通过 ${pass} / 失败 ${fail}`);
  process.exitCode = fail ? 1 : 0;
};

try {
  eval(js + driver);
} catch (e) {
  console.error("脚本执行失败:", e.message, "\n", e.stack.split("\n").slice(0, 4).join("\n"));
  process.exit(1);
}
