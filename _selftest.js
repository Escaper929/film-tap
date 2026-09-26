// 把 index.html 里的脚本抽出来，在 Node 里配上最小 DOM 桩跑一遍所有视图，
// 用来验证渲染路径不会抛异常、且能产出内容。
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");

/* 凭据回归护栏：这个项目是要发布出去的，源码里绝不允许出现任何凭据或内网地址。
   （上一个项目就是因为把 NAS 账号密码硬编码进 app.js 并推上公开仓库才泄露的。）

   ⚠️ 护栏自己也只能写「泛化的形状」，绝不能把真实密码 / 内网 IP 写成字面量 ——
   否则护栏本身就是新的泄露点。这个坑真踩过：把密码原样写进 mustNot 数组里，
   文件一提交，等于换个地方继续公开它（而且是在一个专门用来防泄露的文件里）。
   所以下面这些规则描述的只有「长什么样」，不包含任何真实值。 */
const CRED_RULES = [
  [/\b192\.168\.\d{1,3}\.\d{1,3}\b/,                       "私有网段 192.168.x.x"],
  [/\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/,                    "私有网段 10.x.x.x"],
  [/\b172\.(1[6-9]|2[0-9]|3[01])\.\d{1,3}\.\d{1,3}\b/,     "私有网段 172.16–31.x.x"],
  [/WEBDAV_AUTH\s*=\s*['"][^'"]+['"]/,                     "硬编码的 WebDAV 凭据"],
  [/Authorization['"]?\s*[:=]\s*['"]Basic\s+[A-Za-z0-9+/=]{8,}/, "硬编码的 Basic 认证头"],
  /* GitHub 令牌的形状。项目现在会把数据写进一个私有仓库，所以「令牌被写进源码」
     成了新的头号风险：ghp_ = classic PAT，gho_ = OAuth，ghs_ = 服务令牌，
     github_pat_ = fine-grained。只描述形状，不含任何真实值。 */
  [/\bghp_[A-Za-z0-9]{20,}/,                               "硬编码的 GitHub classic 令牌"],
  [/\bgho_[A-Za-z0-9]{20,}/,                               "硬编码的 GitHub OAuth 令牌"],
  [/\bghs_[A-Za-z0-9]{20,}/,                               "硬编码的 GitHub 服务令牌"],
  [/\bgithub_pat_[A-Za-z0-9_]{20,}/,                       "硬编码的 GitHub 细粒度令牌"]
];

/* 护栏要覆盖仓库里所有发得出去的文本文件。
   关键：**不能只看站点上打得开的文件**。上次的泄露就发生在 _selftest.js 里 ——
   GitHub Pages 会跳过下划线开头的文件（站点上 404），但它在公开仓库里照样能拿到。
   也就是说，真正的暴露面是「仓库可见范围」，不是「站点可访问范围」。 */
const SHIPPED = [
  "index.html", "sw.js", "manifest.webmanifest",
  "README.md", "_selftest.js", "_shots.py", "_firstrun.py",
  "model/build_fob.py", "model/fob.scad", "model/preview.py", "model/render.py"
];
for (const f of SHIPPED) {
  const p = path.join(__dirname, f);
  if (!fs.existsSync(p)) continue;
  const text = f === "index.html" ? html : fs.readFileSync(p, "utf8");
  for (const [re, why] of CRED_RULES) {
    const hit = text.match(re);
    if (hit) {
      console.error("❌ " + f + " 里出现了「" + why + "」，拒绝通过：", hit[0]);
      process.exit(1);
    }
  }
}

const m = html.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { console.error("找不到 <script>"); process.exit(1); }
const js = m[1];

/* ── 最小 DOM / 浏览器桩 ── */
const store = {};
const els = {};
let loc = { origin: "https://film.example.com", protocol: "https:",
            pathname: "/", search: "", hash: "" };

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

/* fetch 默认不打桩：万一有代码在启动路径上真的想发请求，会立刻炸出来，
   而不是被静默吞掉。需要网络的用例自己在驱动里设 global.fetch。 */
global.fetch = () => Promise.reject(new Error("测试里没有打桩 fetch"));

/* ── 注入测试驱动 ── */
const driver = String.raw`
save(demoData());

const CASES = [
  { name:"机库首页",    search:"",       hash:"#/",
    must:["Leica M6","尼康 FM2","奥林巴斯 XA2","禄来 3.5F","214 天","示例数据","36 张"],
    mustNot:[] },
  { name:"碰 m6 打开",  search:"?c=m6",  hash:"",
    must:["Kodak Portra 400","EI 1600","推 2 档","92<small>天</small>",
          "135 · 36 张","换一卷","编辑这卷"],
    mustNot:["该冲了","过片","还剩 12 张"] },
  { name:"120 机身",    search:"",       hash:"#/c/rollei",
    must:["禄来 3.5F","120 · 12 张","Ilford Delta 100"],
    mustNot:["还剩"] },
  { name:"久置的卷",    search:"",       hash:"#/c/xa2",
    must:["Kodak ColorPlus 200","214 天","该冲了","偏久"],
    mustNot:[] },
  { name:"未登记新 ID",  search:"",       hash:"#/c/brand-new",
    must:["新挂件","机身名称","画幅","brand-new"],
    mustNot:[] },
  { name:"换卷表单",    search:"",       hash:"#/c/m6/load",
    must:["换一卷","Kodak Portra 400","Ilford HP5 Plus 400","记进历史","总共多少张"],
    mustNot:["已经拍了多少张"] },
  { name:"编辑当前卷",  search:"",       hash:"#/c/m6/edit",
    must:["编辑这卷",'value="1600"',"推到 1600 拍夜景"],
    mustNot:[] },
  { name:"编辑机身",    search:"",       hash:"#/c/m6/setup",
    must:["编辑机身",'value="Leica"','value="M6"',"135","画幅"],
    mustNot:["新挂件"] },
  { name:"历史记录",    search:"",       hash:"#/c/m6/history",
    must:["Ilford HP5 Plus 400","Fujifilm C200","22 天","120 天","36 张"],
    mustNot:["/36 张"] },
  { name:"统计页",      search:"",       hash:"#/stats",
    must:["统计","机身总数","当前有卷","已用胶卷",
          "常用胶卷 Top","Ilford HP5 Plus 400","机身状态"],
    mustNot:["累计快门"] },
  { name:"设置与备份",  search:"",       hash:"#/settings",
    must:["导出备份","已登记的机身 ID","?c=","清空全部数据",
          "同步到自己的 NAS","WebDAV 目录地址","保存同步设置",
          "同步到私有仓库","访问令牌","保存仓库设置",
          "验证并登录","建一个私有仓库","建一枚 fine-grained 令牌"],
    mustNot:["192.168.","WEBDAV_AUTH","Authorization: Basic","Bearer ",
             "ghp_","gho_","github_pat_"] }
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

/* ══════════════════════════════════════════════════════════
   过片计数已下线（机身自己就能看出拍了多少张，不需要在 App 里再记一遍）。
   删功能最容易留下的不是报错，是**半截残骸**：某个入口没删干净、
   某个视图还在算一个永远不会变的数、老数据里的字段还在被同步来同步去。
   所以这里正面钉住「它真的没了」，而不是只删掉原来的用例。
   ══════════════════════════════════════════════════════════ */
try {
  const noFn = typeof advanceShot === "undefined" && typeof filmBar === "undefined";
  __setLoc("", "#/c/m6"); render();
  const ui = __app();
  const noUi = ui.indexOf("过片") < 0 && ui.indexOf("还剩") < 0 && ui.indexOf('id="f-shots"') < 0;
  __setLoc("", "#/c/m6/edit"); render();
  const noField = __app().indexOf("已经拍了多少张") < 0;
  check("计数功能已摘干净", noFn && noUi && noField,
        "函数已移除=" + noFn + " 机身页无残留=" + noUi + " 表单无输入框=" + noField);
} catch (e) { check("计数功能已摘干净", false, "抛异常 " + e.message); }

/* ── 统计页只该有三格 ──
   statgrid 现在是 3 列，正好排满一行；再多一格就会掉到第二行、
   留出「3 + 1」的空洞。用计数而不是「有没有累计快门几个字」来钉，
   这样以后换掉某一格的名字，这条断言仍然管用。 */
try {
  __setLoc("", "#/stats"); render();
  const ui = __app();
  const n = (ui.match(/<div class="stat">/g) || []).length;
  check("统计页三格排满一行", n === 3, "实际 " + n + " 格");
} catch (e) { check("统计页三格排满一行", false, "抛异常 " + e.message); }

/* ── 老数据里的 shots 会被清掉 ──
   计数下线了，可老库（本机 localStorage、导出的 JSON、私有仓库里的 data.json）
   里还留着 shots。不清掉的话它会一直跟着导出和同步来回跑，
   以后有人看到这个字段会以为它还在生效 —— 而没有任何代码读它。
   注意 total 必须留下：那是「这卷多少张」，和计数是两回事。 */
try {
  localStorage.removeItem("filmtap.v1.corrupt");
  localStorage.setItem("filmtap.v1", JSON.stringify({ version:2, films:{}, cameras:{
    m6:{ id:"m6", name:"Leica M6", format:"135",
         history:[ { stock:"Ilford HP5 Plus 400", shots:36, total:36 } ],
         loaded:{ stock:"Kodak Portra 400", iso:400, ei:null, loadedAt:"2026-01-01",
                  shots:20, total:36, note:"" } } } }));
  const db = load();
  const gone = !("shots" in db.cameras.m6.loaded) &&
               db.cameras.m6.history.every(r => !("shots" in r));
  const kept = db.cameras.m6.loaded.total === 36 &&
               db.cameras.m6.history[0].total === 36;
  check("老数据里的 shots 被清掉", gone && kept,
        "字段清除=" + gone + " 张数保留=" + kept);
  /* 上面把本机换成了手工构造的一台机身，后面的用例还要用示例数据。
     跟「装卷扣库存」一样，谁改动了共用 fixture 谁自己把它还原回去。 */
  save(demoData());
} catch (e) { check("老数据里的 shots 被清掉", false, "抛异常 " + e.message); }

/* ── 装卷归档流程 ── */
try {
  __setLoc("", "#/c/fm2/load");
  render();
  __setValue("#f-stock", "Adox CMS 20 II");
  __setValue("#f-iso", "20");
  __setValue("#f-ei", "");
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

/* ── 总张数留空要回落到机身画幅的默认值 ──
   计数下线之后，「这一卷多少张」是这一卷上唯一一个数字字段，
   而它会出现在机身页的标签和历史记录里。
   留着空、填 0、填了非数字，都不该把它变成 0 ——
   （那样标签会直接不显示，用户还以为装卷失败了。）DEFAULT_FRAMES 那层兜底得真的生效。 */
try {
  const editTotal = v => {
    __setLoc("", "#/c/fm2/edit"); render();
    __setValue("#f-stock", "Kodak Portra 400");
    __setValue("#f-total", v);
    saveLoad("fm2", true);
    return load().cameras.fm2.loaded.total;
  };
  const a = editTotal("")   === 36;    // 留空 → 135 的默认
  const b = editTotal("0")  === 36;    // 填 0 → 同上
  const c = editTotal("24") === 24;    // 填了就照填的来
  check("总张数留空回落到画幅默认", a && b && c,
        "留空=" + a + " 填0=" + b + " 填24=" + c);
} catch (e) { check("总张数留空回落到画幅默认", false, "抛异常 " + e.message); }

/* ══════════════════════════════════════════════════════════
   「验证并登录」按钮必须跟着令牌框亮灭。
   修的是这个真实故障（用户实测报的）：按钮一直亮着、空着也能点，
   点完只回一句「先粘一枚令牌」—— 用户分不清是自己没粘上、
   还是这个功能坏了。在真实浏览器里复现过：框里有东西时代码路径是对的
   （唯一一个 #gh-token、value 读得到、吐司变成「已登录 @probe」），
   错的是它**不告诉用户框是空的**。
   ══════════════════════════════════════════════════════════ */
try {
  localStorage.removeItem("filmtap.github");
  __setLoc("", "#/settings"); render();
  const html  = __app();
  const start = html.indexOf('id="gh-verify" disabled') >= 0;
  const wired = html.indexOf('oninput="syncGhVerifyBtn()"') >= 0;

  __setValue("#gh-token", "");
  syncGhVerifyBtn();
  const emptyStaysOff = __el("#gh-verify").disabled === true;

  __setValue("#gh-token", "github_" + "pat_" + "A".repeat(20));
  syncGhVerifyBtn();
  const filledLightsUp = __el("#gh-verify").disabled === false;

  /* 粘贴时爱带上换行 / 空格。光看「非空」就点亮的话，一个空格也能把按钮点亮，
     然后用户拿到一个语焉不详的 401 —— 空白不算内容。 */
  __setValue("#gh-token", "   \n  ");
  syncGhVerifyBtn();
  const blankStaysOff = __el("#gh-verify").disabled === true;

  check("验证按钮跟着令牌亮灭",
        start && wired && emptyStaysOff && filledLightsUp && blankStaysOff,
        "初始禁用=" + start + " 挂上 oninput=" + wired + " 空着不亮=" + emptyStaysOff +
        " 粘了就亮=" + filledLightsUp + " 纯空白不算=" + blankStaysOff);
} catch (e) { check("验证按钮跟着令牌亮灭", false, "抛异常 " + e.message); }

/* ══════════════════════════════════════════════════════════
   WebDAV 的两个前提，保存时就得挡住。
   https 页面发不出 http 请求 —— 浏览器叫它「混合内容」，拦得非常彻底：
   请求根本不会离开浏览器。于是现象和「NAS 没开机」一模一样，
   用户会跑去查一个根本没坏的网络（用户的实际场景：外网走 IPv6 DDNS，网络本身是通的）。
   ══════════════════════════════════════════════════════════ */
try {
  clearDav();
  __setLoc("", "#/settings"); render();

  __setValue("#dav-url", "http://nas.example.com:5005/dav/film/");
  __setValue("#dav-user", "filmapp");
  __setValue("#dav-pass", "app-password");
  saveDavFromForm();
  const msg1 = __el("#toast").textContent;
  const mix1 = getDav() === null && /https 页面发不出 http 请求/.test(msg1);

  /* 同一份配置换成 https 就该能存下来 —— 否则这条护栏就是「一律不放行」，
     而不是「只挡真正用不了的那种」 */
  __setValue("#dav-url", "https://nas.example.com/dav/film/");
  saveDavFromForm();
  const d1 = getDav();
  const ok2 = !!d1 && d1.url === "https://nas.example.com/dav/film/" && d1.user === "filmapp";

  /* 清空地址 = 清除设置，这条老行为不能被新护栏改掉 */
  __setValue("#dav-url", "");
  saveDavFromForm();
  const ok3 = getDav() === null;

  check("http 地址在 https 页面下被挡", mix1 && ok2 && ok3,
        "拦住=" + mix1 + " https 能存=" + ok2 + " 清空仍可清=" + ok3);
} catch (e) { check("http 地址在 https 页面下被挡", false, "抛异常 " + e.message); }

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

/* ── 界面名字已统一成 film-tap ──
   品牌名只出现在「首页顶栏」和「设置页页脚」；机身相关页面顶栏是返回箭头，
   本来就该显示机身名而不是品牌名。 */
try {
  __setLoc("", "#/");         render(); const home = __app();
  __setLoc("", "#/settings"); render(); const setn = __app();

  const okHome = home.indexOf("film-tap") >= 0;
  const okSet  = setn.indexOf("film-tap") >= 0;
  const okOld  = home.indexOf("胶片簿") < 0 && setn.indexOf("胶片簿") < 0;
  // 静态 HTML 里也不能再留旧名（含 <title> 与 iOS 主屏名）
  const okHtml = html.indexOf("胶片簿") < 0 && html.indexOf("<title>film-tap") >= 0;

  check("界面名字统一", okHome && okSet && okOld && okHtml,
        "首页=" + okHome + " 设置页=" + okSet +
        " 无旧名=" + okOld + " 静态页=" + okHtml);
} catch (e) { check("界面名字统一", false, "抛异常 " + e.message); }

/* ── 改名迁移：只留老键时，本机数据必须接过来 ──
   项目从 film-nfc 改名成 film-tap，localStorage 键跟着换。
   迁移写错 = 用户一觉醒来机身全没了，所以这条必须钉死。 */
try {
  localStorage.removeItem("filmtap.v1");
  localStorage.setItem("filmnfc.v1", JSON.stringify({
    version: 2,
    cameras: { legacy1: { id: "legacy1", name: "迁移来的机身", format: "135",
                          loaded: null, history: [] } }
  }));

  const d1  = load();
  const ok1 = !!(d1.cameras.legacy1 && d1.cameras.legacy1.name === "迁移来的机身");
  const ok2 = localStorage.getItem("filmtap.v1") !== null;      // 已落到新键

  // 两个键都有数据时必须新键优先，绝不能被老键盖回去
  localStorage.setItem("filmtap.v1", JSON.stringify({
    version: 2,
    cameras: { new1: { id: "new1", name: "新键的机身", format: "135",
                       loaded: null, history: [] } }
  }));
  const d2  = load();
  const ok3 = !!d2.cameras.new1 && !d2.cameras.legacy1;

  check("改名迁移（本机数据）", ok1 && ok2 && ok3,
        "接过来=" + ok1 + " 落新键=" + ok2 + " 新键优先=" + ok3);
} catch (e) { check("改名迁移（本机数据）", false, "抛异常 " + e.message); }

/* ── 改名迁移：已填好的 NAS 设置同样不能丢 ── */
try {
  localStorage.removeItem("filmtap.webdav");
  localStorage.setItem("filmnfc.webdav",
    JSON.stringify({ url: "https://nas.example.com/dav/x/", user: "u", pass: "p" }));

  const c   = getDav();
  const ok1 = !!c && c.url === "https://nas.example.com/dav/x/" && c.user === "u";
  const ok2 = localStorage.getItem("filmtap.webdav") !== null;

  check("改名迁移（NAS 设置）", ok1 && ok2, "读老键=" + ok1 + " 落新键=" + ok2);
} catch (e) { check("改名迁移（NAS 设置）", false, "抛异常 " + e.message); }

/* ── 库存页与汇总 ── */
try {
  save(demoData());
  __setLoc("", "#/films"); render();
  const ui = __app();
  const t  = filmTotals(load());

  const ok1 = t.kinds === 6 && t.rolls === 31;
  const ok2 = ui.indexOf("Kodak Portra 400") >= 0 && ui.indexOf("Fujifilm Pro 400H") >= 0;
  const ok3 = ui.indexOf("已过期") >= 0;                                  // Pro 400H 那条的标签
  const ok4 = ui.indexOf("共 31 卷") >= 0;

  const list = filmList(load());
  const ok5  = list[0].stock === "Fujifilm Pro 400H";                      // 最快到期的排最前
  const p400 = list.filter(f => f.stock === "Kodak Portra 400");
  const ok6  = p400.length === 2 && p400[0].format !== p400[1].format;    // 同款不同画幅 = 两条

  /* 单价和购入日期都必须在列表里看得见 —— 这两项一开始被塞进那一行小字里，
     结果被省略号吃掉了，界面上等于没有。钉住「不许再退回省略号」。 */
  const ok7 = ui.indexOf("¥78/卷") >= 0 && ui.indexOf("购于 ") >= 0;
  const rows = ui.split('class="roll" style="white-space:normal"').length - 1;
  const ok8  = rows === list.length;

  check("库存页与汇总", ok1 && ok2 && ok3 && ok4 && ok5 && ok6 && ok7 && ok8,
        "款数/卷数=" + ok1 + " 列出型号=" + ok2 + " 过期标签=" + ok3 +
        " 汇总文案=" + ok4 + " 排序=" + ok5 + " 同款分画幅=" + ok6 +
        " 单价可见=" + ok7 + " 不截断=" + ok8);
} catch (e) { check("库存页与汇总", false, "抛异常 " + e.message); }

/* ── 库存增删与合并 ── */
try {
  save(demoData());
  const before = load().films["kodak-portra-400@135"].count;              // 示例里是 8

  /* 新增一条「同款同画幅」：应当数量相加，而不是变成第 7 条 */
  __setLoc("", "#/films/new"); render();
  __setValue("#ff-key", "");
  __setValue("#ff-stock", "Kodak Portra 400");
  __setValue("#ff-format", "135");
  __setValue("#ff-count", "4");
  __setValue("#ff-exp", "");
  __setValue("#ff-bought", "");
  __setValue("#ff-price", "");
  __setValue("#ff-note", "");
  saveFilm();
  const m   = load().films["kodak-portra-400@135"];
  const ok1 = m.count === before + 4;
  const ok2 = m.exp === monthsAhead(9) && m.note === "冰箱冷藏";           // 表单留空 → 保住原值
  const ok3 = Object.keys(load().films).length === 6;

  /* 改画幅 → 键变了，不该留下幽灵条目 */
  __setLoc("", "#/films/edit/kodak-ektar-100@135"); render();
  __setValue("#ff-key", "kodak-ektar-100@135");
  __setValue("#ff-stock", "Kodak Ektar 100");
  __setValue("#ff-format", "120");
  __setValue("#ff-count", "5");
  saveFilm();
  const ok4 = !load().films["kodak-ektar-100@135"] && !!load().films["kodak-ektar-100@120"];

  /* 删除 */
  __setLoc("", "#/films/edit/kodak-ektar-100@120"); render();
  __setValue("#ff-key", "kodak-ektar-100@120");
  deleteFilm();
  const ok5 = !load().films["kodak-ektar-100@120"];

  check("库存增删与合并", ok1 && ok2 && ok3 && ok4 && ok5,
        "同款相加=" + ok1 + " 留空保原值=" + ok2 + " 未新增条目=" + ok3 +
        " 改键无幽灵=" + ok4 + " 可删除=" + ok5);
} catch (e) { check("库存增删与合并", false, "抛异常 " + e.message); }

/* ── 表格解析：引号、字段里的分隔符、BOM、CRLF、分隔符嗅探 ── */
try {
  /* ⚠️ 下面这段驱动本身是外层模板字符串的一部分，所以字符串里的反斜杠要写**两遍**：
     直接写 \r 会被外层模板先吃成真的回车，驱动源码就在字符串中间断掉，报
     "Invalid or unexpected token"。写成 \r 外层先还原成一个反斜杠，
     驱动再把它当转义解释，才是我们想要的 CRLF。正则里的 \d 同理，要写 \d。 */
  const csv = '\uFEFF型号,数量,画幅,备注\r\n'
            + 'Kodak Portra 400,12,135,"冰箱冷藏, 别晒"\r\n'
            + '"Fuji, 分装",3,120,""\r\n\r\n';
  const rows = parseDelimited(csv, sniffDelim(csv));

  const ok1 = rows.length === 3;                                          // 尾部空行被丢掉
  const ok2 = rows[0].join("|") === "型号|数量|画幅|备注";                  // BOM 不会黏在表头上
  const ok3 = rows[1][3] === "冰箱冷藏, 别晒";                             // 引号里带分隔符
  const ok4 = rows[2][0] === "Fuji, 分装";                                 // 型号本身带逗号
  const ok5 = sniffDelim("a\tb\tc\n1\t2\t3") === "\t";                // 从 Excel 粘贴是制表符
  const ok6 = sniffDelim("a;b;c\n1;2;3") === ";";                        // 欧洲区 Excel 用分号
  const ok7 = parseDelimited('a,"说 ""引号"" 的写法"', ",")[0][1] === '说 "引号" 的写法';

  check("表格解析", ok1 && ok2 && ok3 && ok4 && ok5 && ok6 && ok7,
        "行数=" + ok1 + " 表头干净=" + ok2 + " 引导内分隔符=" + ok3 + " 型号带逗号=" + ok4 +
        " 制表符=" + ok5 + " 分号=" + ok6 + " 转义引号=" + ok7);
} catch (e) { check("表格解析", false, "抛异常 " + e.message); }

/* ── 列自动识别 ── */
try {
  const m1 = mapImport(["胶卷型号 (必填)", "库存数量", "规格", "有效期至", "购买日期", "单价(元)", "备注"]);
  const ok1 = m1.stock === 0 && m1.count === 1 && m1.format === 2 &&
              m1.exp === 3 && m1.boughtAt === 4 && m1.price === 5 && m1.note === 6;

  const m2 = mapImport(["Film Stock", "Qty", "Format", "Expiry", "Price"]);
  const ok2 = m2.stock === 0 && m2.count === 1 && m2.format === 2 && m2.exp === 3 && m2.price === 4;

  /* 「有效期」不能被 buyingAt 的「日期」别名抢走 */
  const m3 = mapImport(["型号", "数量", "有效期", "购入日期"]);
  const ok3 = m3.exp === 2 && m3.boughtAt === 3;

  /* 认不出来就别硬认，交给用户在下拉里指定 */
  const m4 = mapImport(["列一", "列二"]);
  const ok4 = m4.stock == null && m4.count == null;

  check("列自动识别", ok1 && ok2 && ok3 && ok4,
        "中文长表头=" + ok1 + " 英文=" + ok2 + " 有效期归位=" + ok3 + " 不乱认=" + ok4);
} catch (e) { check("列自动识别", false, "抛异常 " + e.message); }

/* ── 字段归一化：画幅、日期、数字 ── */
try {
  const ok1 = normFormat("", "Kodak Portra 400 120") === "120";
  const ok2 = normFormat("135", "随便写") === "135";
  const ok3 = normFormat("", "Ilford Delta 3200") === "135";     // "3200" 里不能看出 "120"
  const ok4 = normFormat("4x5", "") === "大画幅" && normFormat("页片", "") === "大画幅";
  const ok5 = normMonth("2027-06") === "2027-06" && normMonth("2027/6") === "2027-06" &&
              normMonth("2027年6月") === "2027-06" && normMonth("202706") === "2027-06";
  const ok6 = normDate("2026/3/12") === "2026-03-12" && normDate("2026-03") === "2026-03-01";
  const ok7 = normNum("¥78.5 元") === 78.5 && normNum("1,280") === 1280 && normNum("") === null;
  const ok8 = /^\d{4}-\d{2}$/.test(normMonth("46000"));         // Excel 存的日期序列号
  const ok9 = normMonth("不知道") === "" && normMonth("") === "";  // 看不懂就不猜

  check("字段归一化", ok1 && ok2 && ok3 && ok4 && ok5 && ok6 && ok7 && ok8 && ok9,
        "型号带120=" + ok1 + " 显式优先=" + ok2 + " 3200不误判=" + ok3 + " 大画幅=" + ok4 +
        " 月份=" + ok5 + " 日期=" + ok6 + " 数字=" + ok7 + " 序列号=" + ok8 + " 不乱猜=" + ok9);
} catch (e) { check("字段归一化", false, "抛异常 " + e.message); }

/* ── 导入：覆盖 / 累加 / 不抹掉表里没有的字段 ── */
try {
  save(demoData());
  const rows = parseDelimited(
    "型号,数量,画幅,有效期\n" +
    "Kodak Portra 400,20,135,2028-01\n" +      // 已存在 → 覆盖成 20
    "Kodak Ektar 100,7,135,\n" +               // 已存在 → 覆盖成 7
    "Lomography Color 100,5,135,2028-09\n" +   // 新增
    ",3,135,\n",                               // 没型号 → 跳过
    ",");
  const map = mapImport(rows[0]);

  const db = load();
  const r1 = applyImport(db, rows.slice(1), map, "replace");
  const ok1 = r1.added === 1 && r1.merged === 2 && r1.skipped === 1;
  const ok2 = db.films["kodak-portra-400@135"].count === 20;
  const ok3 = db.films["kodak-portra-400@135"].note === "冰箱冷藏";   // 表里没有备注列 → 保住原值
  const ok4 = db.films["kodak-portra-400@135"].exp === "2028-01";    // 表里有 → 覆盖
  const ok5 = !!db.films["lomography-color-100@135"];
  save(db);

  const db2 = load();
  const r2 = applyImport(db2, rows.slice(1), map, "add");
  const ok6 = r2.added === 0 && db2.films["kodak-portra-400@135"].count === 40;   // 20 + 20
  const ok7 = db2.films["kodak-ektar-100@135"].count === 14;                      // 7 + 7

  check("导入覆盖与累加", ok1 && ok2 && ok3 && ok4 && ok5 && ok6 && ok7,
        "计数=" + ok1 + " 覆盖=" + ok2 + " 保住缺列=" + ok3 + " 覆盖有效期=" + ok4 +
        " 新增=" + ok5 + " 累加=" + ok6 + " 累加二=" + ok7);
} catch (e) { check("导入覆盖与累加", false, "抛异常 " + e.message); }

/* ── 编码兜底：中文 Windows 的 Excel 导出 CSV 是 GBK，不是 UTF-8 ── */
try {
  /* "型号,数量\nKodak Portra 400,12\n" 的 GBK 字节。
     这段字节是**非法 UTF-8**，所以严格解码一定会抛，兜底路径一定会被走到。 */
  const GBK = [208, 205, 186, 197, 44, 202, 253, 193, 191, 10, 75, 111, 100, 97, 107,
               32, 80, 111, 114, 116, 114, 97, 32, 52, 48, 48, 44, 49, 50, 10];
  const text = decodeBytes(new Uint8Array(GBK).buffer);
  const rows = parseDelimited(text, sniffDelim(text));
  const ok1 = text.indexOf("型号,数量") === 0;
  const ok2 = rows.length === 2 && rows[1][0] === "Kodak Portra 400" && rows[1][1] === "12";

  /* UTF-8 + BOM 也要认（Excel 的另一种导出） */
  const u8 = new TextEncoder().encode("\uFEFF型号,数量\nIlford HP5 Plus 400,9\n");
  const r2 = parseDelimited(decodeBytes(u8.buffer), ",");
  const ok3 = r2[0][0] === "型号" && r2[1][1] === "9";

  check("编码兜底（GBK）", ok1 && ok2 && ok3,
        "GBK 认出来=" + ok1 + " 行对=" + ok2 + " UTF-8+BOM=" + ok3);
} catch (e) { check("编码兜底（GBK）", false, "抛异常 " + e.message); }

/* ══════════════════════════════════════════════════════════
   机身 ID 不能逃出 onclick —— 这是本项目最大的一个注入面。
   ID 从 URL 上的 ?c= 来，而那正是 NFC 标签里写的内容，标签可能是别人给的；
   它又会被拼进 onclick 属性里（saveSetup('…') / copySlip('…') / go('…')）。
   ⚠️ encodeURIComponent 挡不住这件事（它不编码 ! ' ( ) * ），
      而 esc() 也挡不住（&#39; 会被 HTML 解析器还原成 '，字符串照样在 JS 里被闭掉）。

   所以断言不能只看「渲染结果里有没有那几个字」—— 转义之后那几个字还在。
   得把拼出来的那段 JS 真的喂给引擎跑一遍，看它是不是只调了一次处理函数、
   有没有碰到 document。这才能区分「转义了」和「没转义」。
   ══════════════════════════════════════════════════════════ */
try {
  /* onclick 里会出现的处理函数名。把它们做成 new Function 的具名参数，
     被测代码里引用到的名字就都会解析到替身，而不是去全局找真的实现。 */
  const PARAMS = ["go","addCamera","loadDemo","pickFormat","copySlip",
    "saveSetup","saveLoad","clearFilm","pickFilm","setFilmFormat","setImportMode",
    "runImport","resetImport","importFromPaste","onImportFile","setImportMap",
    "exportJSON","importJSON",
    "saveDavFromForm","syncToNas","restoreFromNas","testDav","clearDavSettings","saveGhFromForm",
    "ghPush","ghPull","toggleGhAuto","clearGhSettings","wipe","exportCorrupt",
    "verifyGh","listGhRepos","useGhRepo","syncGhVerifyBtn",
    "dropCorrupt","deleteFilm","saveFilm","updatePP","document"];

  /* 属性在交给 JS 之前，浏览器会先把 HTML 实体还原成字符 */
  function unent(s){
    return String(s).replace(/&quot;/g, String.fromCharCode(34))
                    .replace(/&#39;/g, String.fromCharCode(39))
                    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
  }

  /* 把一份渲染结果里所有 onclick 都跑一遍。
     leak = 有属性跑出了处理函数之外的东西（改了 document，或抛异常，或没调用/调了多次） */
  function runHandlers(html){
    const trip = { title: "safe" };
    const args = [];
    let leak = false, calls = 0, seen = 0;
    /* 顺带把 onchange / oninput 也扫了 —— 现在它们只接数字和 this，
       但哪天有人往里塞了数据，这条断言应该自动管住。 */
    const re = /\bon(?:click|change|input|blur|focus)="([^"]*)"/g;
    let m;
    while ((m = re.exec(html))){
      const code = unent(m[1]);
      const before = calls;
      const feed = PARAMS.map(n => n === "document"
        ? trip
        : (...a) => { calls++; args.push(a); });
      seen++;
      try { new Function(PARAMS.join(","), code).apply(null, feed); }
      catch (e){ leak = true; }
      if (calls - before !== 1) leak = true;      // 每个 onclick 恰好一次调用
      if (trip.title !== "safe") leak = true;     // 有东西在调用之外被执行了
    }
    return { leak, args, seen };
  }

  const EVIL = "x');document.title='PWNED';//";

  /* ① 还没登记的挂件 —— 这条路是 renderSetup，ID 直接落进 saveSetup('…') */
  __setLoc("?c=" + encodeURIComponent(EVIL), "");
  render();
  const r1 = runHandlers(__app());
  const ok1 = !r1.leak && r1.seen > 0;
  /* payload 必须原样、完整地被当成**一个字符串**传下去：
     少一个字符就说明转义把用户的内容吃掉了，那和没转义一样是 bug。 */
  const carried = r1.args.filter(a => typeof a[0] === "string" && a[0].indexOf("PWNED") >= 0);
  const ok2 = carried.length >= 1 && carried.every(a => a[0] === EVIL);

  /* ② 已经登记过的机身，以及库存里带引号的型号 ——
     这两条路上的数据可能来自导入的 JSON 或私有仓库，不经过 parseRoute */
  const db = load();
  db.cameras[EVIL] = { id:EVIL, name:"坏 ID 的机身", format:"135", history:[],
    loaded:{ stock:"Kodak Portra 400", iso:400, ei:null, loadedAt:todayLocal(),
             total:36, note:"" } };
  const EVILFILM = "x');alert(1)//@135";
  db.films = {}; db.films[EVILFILM] = normalizeFilm({ id:EVILFILM,
    stock:"x');alert(1)//", format:"135", count:2 });
  save(db);

  const VIEWS = ["#/", "#/stats", "#/settings", "#/films", "#/films/new",
                 "#/films/import",
                 "#/films/edit/" + encodeURIComponent(EVILFILM),
                 "#/c/" + encodeURIComponent(EVIL),
                 "#/c/" + encodeURIComponent(EVIL) + "/load",
                 "#/c/" + encodeURIComponent(EVIL) + "/edit",
                 "#/c/" + encodeURIComponent(EVIL) + "/history",
                 "#/c/" + encodeURIComponent(EVIL) + "/setup"];
  const bad = [];
  for (const v of VIEWS){
    __setLoc("", v);
    try { render(); } catch (e){ bad.push(v + "(抛异常)"); continue; }
    if (runHandlers(__app()).leak) bad.push(v);
  }

  /* 设置页还有两种状态平时渲染不到，得单独扫：
     ① 已登录且列到了仓库（比未登录那屏多好几个 onclick）
     ② 登录了但还没选仓库（走「列出我的私有仓库」那条分支）
     ⚠️ 扫完必须把 filmtap.github 还原 —— 后面「回灌只在空库发生」那些用例
        依赖「此刻到底有没有配仓库」，留一份假的进去会让它们测出假结果。 */
  const keepGh = localStorage.getItem("filmtap.github");
  const STATES = [
    { note:"已登录+有仓库列表", cfg:{ owner:"o", repo:"r", token:"t", login:"me", auto:false },
      repos:[{ full_name:"o/r", private:true }] },
    { note:"登录未选仓库",      cfg:{ token:"t", login:"me", auto:false }, repos:null }
  ];
  for (const st of STATES){
    saveGh(st.cfg);
    ghRepos = st.repos;
    __setLoc("", "#/settings");
    try { render(); } catch (e){ bad.push("#/settings(" + st.note + ",抛异常)"); continue; }
    if (runHandlers(__app()).leak) bad.push("#/settings(" + st.note + ")");
  }
  ghRepos = null;
  if (keepGh === null) localStorage.removeItem("filmtap.github");
  else                 localStorage.setItem("filmtap.github", keepGh);

  const ok3 = bad.length === 0;

  check("机身 ID 不能逃出 onclick", ok1 && ok2 && ok3,
        "挂件页无泄漏=" + ok1 + " 参数完整=" + ok2 +
        (bad.length ? " 有泄漏的页面=" + bad.join(" ") : " " + VIEWS.length + " 个页面全干净=" + ok3));

  localStorage.removeItem("filmtap.v1.corrupt");
} catch (e) { check("机身 ID 不能逃出 onclick", false, "抛异常 " + e.message); }

/* ══════════════════════════════════════════════════════════
   读不出来的数据不能被静默丢掉。
   以前 load() 解析失败就直接返回空库，界面上和「真的没数据」一模一样；
   用户接着随手记一台机身，save() 就把坏掉的那份永久覆盖了。
   现在要求：原文留一份、界面说清楚、后面的写入不顶掉它。
   ══════════════════════════════════════════════════════════ */
try {
  localStorage.removeItem("filmtap.v1");
  localStorage.removeItem("filmnfc.v1");
  localStorage.removeItem("filmtap.v1.corrupt");

  const broken = '{"version":2,"cameras":{"m6":{"id":"m6","name":"Leica';
  localStorage.setItem("filmtap.v1", broken);

  const d = load();
  const ok1 = Object.keys(d.cameras).length === 0;                    // 读不出来 → 空库
  const ok2 = localStorage.getItem("filmtap.v1.corrupt") === broken;  // 原文留了一份

  __setLoc("", "#/"); render();
  const ok3 = __app().indexOf("没能读出来") >= 0;                      // 首页露横幅
  __setLoc("", "#/settings"); render();
  const ok4 = __app().indexOf("没能读出来") >= 0;                      // 设置页也露

  /* 用户接着记了一台机身 —— 坏掉的那份还在 */
  const d2 = load();
  d2.cameras.newbie = { id:"newbie", name:"新机身", format:"135", loaded:null, history:[] };
  save(d2);
  const ok5 = localStorage.getItem("filmtap.v1.corrupt") === broken;
  const ok6 = JSON.parse(localStorage.getItem("filmtap.v1")).cameras.newbie.name === "新机身";

  /* 已经有留底时，后面更残缺的那次失败不能把它盖掉 */
  localStorage.setItem("filmtap.v1", "{坏");
  load();
  const ok7 = localStorage.getItem("filmtap.v1.corrupt") === broken;

  localStorage.removeItem("filmtap.v1.corrupt");
  check("损坏数据留底不静默丢", ok1 && ok2 && ok3 && ok4 && ok5 && ok6 && ok7,
        "空库=" + ok1 + " 留底=" + ok2 + " 首页横幅=" + ok3 + " 设置横幅=" + ok4 +
        " 写入后仍在=" + ok5 + " 新数据可写=" + ok6 + " 不被残片顶掉=" + ok7);
} catch (e) { check("损坏数据留底不静默丢", false, "抛异常 " + e.message); }

/* ── 历史里混进坏记录：不能整页空白 ──
   以前 normalizeRoll 对 null 返回 null 而数组照留，历史页在 r.stock 上抛异常，
   用户看到的是白屏 —— 比「少一卷」糟得多。 */
try {
  localStorage.removeItem("filmtap.v1.corrupt");
  localStorage.setItem("filmtap.v1", JSON.stringify({ version:2, films:{}, cameras:{
    m6:{ id:"m6", name:"Leica M6", format:"135", loaded:null,
         history:[ null, "这不是一卷", { stock:"Kodak Portra 400", total:36 } ] } } }));
  let threw = false, out = "";
  try { __setLoc("", "#/c/m6/history"); render(); out = __app(); }
  catch (e){ threw = true; }
  const ok1 = !threw && out.indexOf("Kodak Portra 400") >= 0;    // 好的那条还在
  const ok2 = out.indexOf("共 1 卷") >= 0;                        // 坏的两条被剔掉
  localStorage.removeItem("filmtap.v1.corrupt");
  check("历史里的坏记录不炸页面", ok1 && ok2, "不抛异常=" + !threw + " 好记录还在=" + ok1 + " 坏记录剔掉=" + ok2);
} catch (e) { check("历史里的坏记录不炸页面", false, "抛异常 " + e.message); }

/* ── 分隔符嗅探：第一行经常是不可靠的 ──
   真实表格第一行可能是个跨列大标题（只有 1 列）。只看首行的话，
   制表符文件四种分隔符都切出 1 列，会退化成默认的逗号，整张表就废了。 */
try {
  const ok1 = sniffDelim("胶片库存\n型号\t数量\nPortra 400\t5") === "\t";
  const ok2 = sniffDelim("型号,数量\nPortra 400,5") === ",";
  const ok3 = sniffDelim("型号;数量\nPortra 400;5") === ";";
  const ok4 = sniffDelim("单列\n只有这样") === ",";              // 都切不开 → 退化成逗号
  check("分隔符嗅探不被标题行骗到", ok1 && ok2 && ok3 && ok4,
        "标题行+制表符=" + ok1 + " 逗号=" + ok2 + " 分号=" + ok3 + " 退化=" + ok4);
} catch (e) { check("分隔符嗅探不被标题行骗到", false, "抛异常 " + e.message); }

/* ── 装卷扣库存 ── */
try {
  save(demoData());
  __setLoc("", "#/c/m6/load"); render();
  __setValue("#f-stock", "Ilford HP5 Plus 400");
  __setValue("#f-iso", "400");
  __setValue("#f-ei", "");
  __setValue("#f-total", "36");
  __setValue("#f-date", "2026-09-24");
  __setValue("#f-note", "");
  saveLoad("m6", false);
  const ok1 = load().films["ilford-hp5-plus-400@135"].count === 11;    // 12 → 11

  /* 编辑这一卷不是新装，不该再扣一次 */
  saveLoad("m6", true);
  const ok2 = load().films["ilford-hp5-plus-400@135"].count === 11;

  /* 库存里没有的型号：不能因为库存对不上就拦住装卷 */
  __setLoc("", "#/c/fm2/load"); render();
  __setValue("#f-stock", "没有入库的某个卷");
  __setValue("#f-total", "36");
  saveLoad("fm2", false);
  const fm2 = load().cameras.fm2.loaded;
  const ok3 = !!fm2 && fm2.stock === "没有入库的某个卷";

  check("装卷扣库存", ok1 && ok2 && ok3,
        "扣一卷=" + ok1 + " 编辑不重扣=" + ok2 + " 不对库存也能装=" + ok3);
} catch (e) { check("装卷扣库存", false, "抛异常 " + e.message); }

/* ── 护栏自测：护栏必须真的会响，否则等于没有 ──
   ⚠️ 样本必须**在运行时拼出来**，不能在源码里写成字面量。
   因为 _selftest.js 本身也在被扫描的文件列表里（上次泄露就在这个文件）。
   写成字面量会两头不讨好：要么护栏把自己拦下来，要么为了让它通过而
   把这个文件从扫描列表里去掉 —— 那样真令牌就能藏在最该被检查的地方。
   拼出来之后，文件里出现的是 "10" + "." + ... 这样的碎片，任何规则都不命中。 */
try {
  const oct = ["10", "11", "12", "13"].join(".");            // 编造的私网地址碎片
  const BAD_SAMPLES = [
    'var u = "http://' + oct + ':5005/dav"',
    "WEBDAV" + "_AUTH = 'user:secret'",
    'headers:{Authorization:"Ba' + 'sic dXNlcjpwYXNz"}',
    "token = " + "ghp_" + "A1b2C3d4E5f6G7h8I9j0".repeat(2),
    "token = " + "gho_" + "Z9y8X7w6V5u4T3s2R1q0".repeat(2),
    "token = " + "github_" + "pat_" + "Ab1Cd2Ef3Gh4Ij5Kl6Mn7Op8".repeat(2)
  ];
  const GOOD_SAMPLES = [
    "https://nas.example.com/dav/film/",
    'headers.Authorization = "Bearer " + token',            // 正确写法：令牌走变量，不进源码
    "https://api.github.com/repos/Escaper929/film-tap-data/contents/data.json",
    "film-tap",
    "换一卷"
  ];
  const caught = BAD_SAMPLES.every(s => CRED_RULES.some(([re]) => re.test(s)));
  const quiet  = GOOD_SAMPLES.every(s => !CRED_RULES.some(([re]) => re.test(s)));
  check("凭据护栏有效", caught && quiet,
        "能拦截 " + BAD_SAMPLES.filter(s => CRED_RULES.some(([re]) => re.test(s))).length +
        "/" + BAD_SAMPLES.length + " 不误报=" + quiet);
} catch (e) { check("凭据护栏有效", false, "抛异常 " + e.message); }

/* ── Service Worker 缓存版本 ──
   改了 shell 里的文件（最常改的就是 index.html）却忘了动 sw.js，
   浏览器就不会装新 SW —— 它靠 sw.js 的**字节**变化来判断要不要更新。
   结果是线上明明是新版，手机却一直吃旧缓存里的老页面，而且**不报任何错**。
   这个坑真踩过（改完 index.html 直接推，忘了动 sw.js）。

   光靠"记得改"守不住，所以把 shell 内容的指纹写进 sw.js 里，这里算一遍比对：
   对不上就自检失败，逼着人去改 sw.js 那一行 —— 而那一行一改，
   sw.js 的字节就变了，浏览器才终于会去装新 SW。 */
try {
  const crypto = require("crypto");
  const swText = fs.readFileSync(path.join(__dirname, "sw.js"), "utf8");
  const norm = s => s.replace(/\r\n/g, "\n");   // 换行归一化，免得换台机器就误报
  const shell = ["index.html", "manifest.webmanifest"]
        .map(f => norm(fs.readFileSync(path.join(__dirname, f), "utf8"))).join("\n");
  const fp = crypto.createHash("sha256").update(shell, "utf8").digest("hex");

  const mC   = swText.match(/const\s+CACHE\s*=\s*"([^"]+)"/);
  const mF   = swText.match(/const\s+SHELL_FP\s*=\s*"([^"]+)"/);
  const got  = mF ? mF[1] : "";
  const ver  = mC ? mC[1] : "";
  const okVer = /-v\d+$/.test(ver);

  check("SW 缓存版本同步", fp === got && okVer,
        fp === got
          ? "指纹一致 版本号=" + ver
          : "对不上 —— 改了 shell 文件但没同步 sw.js。"
            + "把 sw.js 里的 SHELL_FP 换成 " + fp + "，CACHE 版本号 +1");
} catch (e) { check("SW 缓存版本同步", false, "抛异常 " + e.message); }

/* ══════════════════════════════════════════════════════════
   私有仓库同步（GitHub Contents API）—— 需要网络，所以放在异步段里，
   用打桩的 fetch 跑。验证四件事：中文能完整往返、只有空库才回灌、
   有数据时一个请求都不发、以及令牌只出现在请求头里绝不进请求体。
   ══════════════════════════════════════════════════════════ */
(async () => {
  let PUTS = [], GETS = 0;
  const REMOTE = { version: 2, cameras: {
    r1: { id:"r1", name:"仓库里的机身 禄来 3.5F", format:"120", loaded:null, history:[] }
  } };

  function stubFetch(remote){
    PUTS = []; GETS = 0;
    global.fetch = async (url, opt) => {
      const method = (opt && opt.method) || "GET";
      if (method === "PUT"){
        PUTS.push({ url:String(url), body:JSON.parse(opt.body),
                    auth:(opt.headers || {}).Authorization });
        return { ok:true, status:200, json: async () => ({ content:{ sha:"s2" } }) };
      }
      GETS++;
      if (!remote) return { ok:false, status:404, json: async () => ({}) };
      return { ok:true, status:200,
               json: async () => ({ content:b64encode(JSON.stringify(remote)), sha:"s1" }) };
    };
  }

  /* load() 在新键缺失时会回头读改名前的旧键（filmnfc.v1），
     而上面的迁移用例往旧键里塞过数据 —— 所以「清空本机」必须两个键一起清，
     否则本机看着是空的、实际不空，回灌就被正确地拒绝了，测出来像是功能坏了。 */
  const clearLocal = () => {
    localStorage.removeItem("filmtap.v1");
    localStorage.removeItem("filmnfc.v1");
  };

  try {
    /* ── base64 必须能带中文往返（机身名和备注都是中文）── */
    const zh = "禄来 3.5F · 富士 C200 · 「推到 1600 拍夜景」";
    const okB64 = b64decode(b64encode(zh)) === zh;
    check("base64 中文往返", okB64, okB64 ? zh.length + " 字符原样返回" : "往返不一致");

    /* ── 设置只落本机、令牌留空保留、默认值补齐 ── */
    __setLoc("", "#/settings"); render();
    __setValue("#gh-owner", "Escaper929");
    __setValue("#gh-repo",  "film-tap-data");
    __setValue("#gh-path",  "");
    __setValue("#gh-branch","");
    __setValue("#gh-token", "fine-" + "grained-sample");
    saveGhFromForm();
    const g1 = getGh();
    const okG1 = !!g1 && g1.owner === "Escaper929" && g1.repo === "film-tap-data" &&
                 g1.path === "data.json" && g1.branch === "main";
    __setValue("#gh-token", "");
    saveGhFromForm();
    const okG2 = getGh().token === "fine-" + "grained-sample";
    const ui   = __app();
    const okG3 = ui.indexOf("保存仓库设置") >= 0 && ui.indexOf("立即上传") >= 0 &&
                 ui.indexOf("从仓库拉取") >= 0 && ui.indexOf("自动同步") >= 0;
    check("仓库设置本机存储", okG1 && okG2 && okG3,
          "默认值=" + okG1 + " 留空保留=" + okG2 + " 按钮齐=" + okG3);

    /* ── 本机为空 → 自动回灌；本机有数据 → 一个请求都不发、绝不覆盖 ── */
    clearLocal();
    stubFetch(REMOTE);
    await ghRehydrate();
    const d1 = load();
    const okR1 = !!d1.cameras.r1 && d1.cameras.r1.name === "仓库里的机身 禄来 3.5F";
    const okR2 = d1.cameras.r1.format === "120";

    stubFetch(REMOTE);
    save({ version:2,
           cameras:{ mine:{ id:"mine", name:"本机自己的机身",
                            format:"135", loaded:null, history:[] } },
           films:{ "kodak-portra-400":{ stock:"Kodak Portra 400", count:12, where:"冰箱" } } });
    await ghRehydrate();
    const okR3 = GETS === 0;
    const d2 = load();
    const okR4 = !!d2.cameras.mine && !d2.cameras.r1;
    check("回灌只在空库发生", okR1 && okR2 && okR3 && okR4,
          "拉回=" + okR1 + " 字段完整=" + okR2 + " 有数据时不请求=" + okR3 + " 未覆盖=" + okR4);

    /* ── 上传：内容对、带 sha、令牌只在请求头 ── */
    stubFetch(REMOTE);
    const okP1 = await ghPush(true);
    const put  = PUTS[PUTS.length - 1];
    const sent = JSON.parse(b64decode(put.body.content));
    const okP2 = !!sent.cameras.mine && !sent.cameras.r1;
    const okP3 = put.body.sha === "s1" && put.body.branch === "main";
    const okP4 = put.auth === "Bearer " + getGh().token;
    const okP5 = put.body.content.indexOf("fine-") < 0;      // 令牌绝不能混进请求体
    const okP6 = !!(sent.films && sent.films["kodak-portra-400"].count === 12);
    check("上传内容与令牌不外泄", okP1 && okP2 && okP3 && okP4 && okP5 && okP6,
          "成功=" + okP1 + " 内容对=" + okP2 + " 带sha=" + okP3 +
          " 只在头=" + okP4 + " 不进body=" + okP5 + " 库存跟着走=" + okP6);

    /* ── 同步层是「整库快照」，它不认识任何业务字段 ──
       以后往库里加新东西（比如胶卷库存 films），只要做成 DB 的顶层字段，
       就自动获得：上传、空库回灌、JSON 导出导入、NAS 同步，一行同步代码都不用改。
       反过来说，如果 load/save/normalize 会削掉不认识的字段，
       新数据就会被同步悄悄丢掉、且不报任何错 —— 所以这条必须钉住。 */
    const dbx = load();
    dbx.films = { "kodak-portra-400": { stock:"Kodak Portra 400", count:12, where:"冰箱" } };
    save(dbx);
    const again = load();
    const okF1 = !!(again.films && again.films["kodak-portra-400"].count === 12);
    const okF2 = !!normalize(JSON.parse(JSON.stringify(again))).films;
    const okF3 = JSON.stringify(again).indexOf("冰箱") >= 0;   // 导出备份也带得走
    check("新字段自动跟着同步", okF1 && okF2 && okF3,
          "活过 load/save=" + okF1 + " 活过 normalize=" + okF2 + " 进导出=" + okF3);

    /* ── 自动同步开关的语义 ── */
    let g = getGh(); g.auto = false; saveGh(g);

    clearLocal();
    stubFetch(REMOTE);
    await ghRehydrate();
    const okA1 = !!load().cameras.r1;                        // 关着也回灌：防丢数据的底线

    let queued = null;
    const realSetTimeout = global.setTimeout;
    global.setTimeout = (fn) => { queued = fn; return 1; };
    const putCount = PUTS.length;
    save(load());
    const okA2 = queued === null;                            // 关着不排队
    const okA3 = PUTS.length === putCount;                   // 关着不上传

    g = getGh(); g.auto = true; saveGh(g);
    queued = null;
    save(load());
    global.setTimeout = realSetTimeout;
    const okA4 = typeof queued === "function";               // 开着会排队
    if (okA4) await queued();
    const okA5 = PUTS.length === putCount + 1;               // 排队的那次真的发出去了
    check("自动同步开关语义", okA1 && okA2 && okA3 && okA4 && okA5,
          "关着也回灌=" + okA1 + " 关着不排队=" + okA2 + " 关着不上传=" + okA3 +
          " 开着排队=" + okA4 + " 开着上传=" + okA5);

    /* ── 点「同步到 NAS」不该顺带动私有仓库 ──
       早先 syncToNas() 先把 savedAt 写回本机再 save()，而 save() 在「自动同步」
       开着时会排一次上传 —— 用户点的是 NAS 那个按钮，动到的却是 GitHub 那个仓库。

       ⚠️ 不能靠 await flushGhPush() 来收尾：这个自检把 setTimeout 桩成了 () => 0，
          于是 flushGhPush 里的「有没有排队」永远为假，测试会**假装通过**。
          所以这里自己把窗口里排上的回调全抓下来跑掉 —— 真排了上传的话，
          这一跑就会打到 api.github.com。
          ⚠️ 不能只看「抓到回调没有」：toast() 自己也用 setTimeout，
             抓到它纯属正常。判定必须落到「那个 PUT 去了哪个域名」。 */
    saveDav({ url:"https://nas.example.com/dav/film/", user:"u", pass:"p" });
    g = getGh(); g.auto = true; saveGh(g);
    stubFetch(REMOTE);

    const pending = [];
    global.setTimeout = (fn) => { pending.push(fn); return 1; };
    await syncToNas();
    global.setTimeout = realSetTimeout;
    for (const fn of pending){ try{ await fn(); }catch(e){} }

    const wentNas = PUTS.some(p => p.url.indexOf("nas.example.com") >= 0);
    const wentGh  = PUTS.filter(p => p.url.indexOf("api.github.com") >= 0).length;
    const okN1 = wentNas && wentGh === 0;
    const okN2 = load().savedAt == null;          // savedAt 也不该被写进本机
    check("NAS 同步不触达私有仓库", okN1 && okN2,
          "写到 NAS=" + wentNas + " 写仓库次数=" + wentGh + " 本机无 savedAt=" + okN2);

    clearGh();
    const okC = getGh() === null;
    check("仓库设置可清除", okC, "清掉=" + okC);

    /* ── 「验证并登录」：把一枚令牌换成身份，数据仓库从列表里选 ──
       这条路的存在意义就是「陌生人不用手打 owner / repo」。所以两个性质必须钉住：
       ① 验证通过只说明「这枚令牌是谁的」，**没选仓库之前同步层不能认它** ——
          否则会拿一份缺 owner/repo 的配置去拼 api.github.com 的地址；
       ② 列不出仓库时必须能退回手填，不能把用户卡在这一屏。 */
    clearGh();
    ghRepos = null;

    const PAT   = "fine-" + "grained-sample";
    const ME    = { login:"octocat", name:"Mona Lisa",
                    avatar_url:"https://avatars.example.com/u/1" };
    const REPOS = [
      { full_name:"octocat/film-tap-data", private:true  },
      { full_name:"octocat/blog",          private:false },
      { full_name:"octocat/film-tap-bak",  private:true  }
    ];
    /* 「远程仓库里的数据」+ 它被读了几次 —— 后半段要用次数证明
       「本机有数据时不会去读远端」，而不是只看结果对不对。 */
    const REMOTE_DB = { version:2, films:{}, cameras:{
      x1:{ id:"x1", name:"仓库里的机器", format:"135", loaded:null, history:[] } } };
    let contentsGets = 0;

    const ghStub = reposOk => async (url, opt) => {
      const u = String(url), h = (opt && opt.headers) || {};
      if (h.Authorization !== "Bearer " + PAT) return { ok:false, status:401, json: async()=>({}) };
      if (u.indexOf("/user/repos") >= 0) {
        return reposOk ? { ok:true, status:200, json: async()=>REPOS }
                       : { ok:false, status:403, json: async()=>({}) };
      }
      if (u.indexOf("/user") >= 0) return { ok:true, status:200, json: async()=>ME };
      if (u.indexOf("/contents/") >= 0) {
        contentsGets++;
        return { ok:true, status:200,
                 json: async()=>({ content:b64encode(JSON.stringify(REMOTE_DB)), sha:"s9" }) };
      }
      return { ok:false, status:404, json: async()=>({}) };
    };

    __setLoc("", "#/settings"); render();
    global.fetch = ghStub(true);
    __setValue("#gh-token", PAT);
    await verifyGh();

    const who  = getGhRaw();
    const okL1 = !!who && who.login === "octocat" && who.avatar === ME.avatar_url;
    const okL2 = getGh() === null;                 // 还没选仓库 → 同步层不认

    render();
    const uiL  = __app();
    const okL3 = uiL.indexOf("gh-pick") >= 0 && uiL.indexOf("octocat/film-tap-data") >= 0;
    /* 公开仓库不能列进来 —— 数据进公开仓库等于把机身和胶卷清单公开 */
    const okL4 = uiL.indexOf("octocat/blog") < 0 && uiL.indexOf("octocat/film-tap-bak") >= 0;

    __setValue("#gh-pick", "octocat/film-tap-data");
    await useGhRepo();
    const gd   = getGh();
    const okL5 = !!gd && gd.owner === "octocat" && gd.repo === "film-tap-data" &&
                 gd.path === "data.json" && gd.branch === "main" && gd.login === "octocat";

    /* fine-grained 令牌只勾了一个仓库时，GitHub 可能不给列 → 退回手填，不卡死 */
    ghRepos = null;
    global.fetch = ghStub(false);
    await listGhRepos();
    const okL6 = Array.isArray(ghRepos) && ghRepos.length === 0;

    /* 手填那条路：令牌已经存着了，不该被要求再粘一次 */
    __setLoc("", "#/settings"); render();
    __setValue("#gh-owner",  "octocat");
    __setValue("#gh-repo",   "film-tap-data-2");
    __setValue("#gh-path",   "");
    __setValue("#gh-branch", "");
    __setValue("#gh-token",  "");
    saveGhFromForm();
    const g2   = getGh();
    const okL7 = !!g2 && g2.repo === "film-tap-data-2" && g2.token === PAT &&
                 g2.path === "data.json" && g2.login === "octocat";

    check("登录拿身份 / 选仓库", okL1 && okL2 && okL3 && okL4 && okL5 && okL6 && okL7,
          "身份=" + okL1 + " 未选仓库不认=" + okL2 + " 下拉出仓库=" + okL3 +
          " 只列私有=" + okL4 + " 选中落配置=" + okL5 + " 列不出不炸=" + okL6 +
          " 手填不重粘令牌=" + okL7);

    /* ── 「本机空 + 仓库非空」这一次上传必须被拦下 ──
       整库快照后写赢、没有合并，这一下就是**把仓库清空**。
       真实路径：本机存储被系统清了（或换了台设备打开），用户随手记一台就冲掉仓库，
       而且不报任何错。自检里这条也顺带守着「别为了拦它把正常上传一起拦掉」。

       ⚠️ 断言要落在「有没有真的发出 PUT」上，不能只看返回值 ——
          返回 false 但请求已经发出去，是另一种更隐蔽的坏法。 */
    saveGh({ owner:"o", repo:"r", token:PAT, path:"data.json", branch:"main", auto:false });
    clearLocal();
    stubFetch(REMOTE);
    const putBefore  = PUTS.length;
    const okE1 = (await ghPush(false)) === false;
    const okE2 = PUTS.length === putBefore;          // 一个字节都没发出去

    /* 这道闸必须够窄：本机有数据时照旧能传上去 */
    save({ version:2, films:{},
           cameras:{ a:{ id:"a", name:"甲", format:"135", loaded:null, history:[] } } });
    stubFetch(REMOTE);
    const okE3 = (await ghPush(true)) === true;
    const okE4 = PUTS.length === 1;

    /* 但「清空全部数据 → 连仓库一起清」必须放行 —— 那条路本来就是先清本机
       再推一份空的上去，正好命中「本机空、仓库非空」。
       不放行的话那个功能会**静默失效**：用户点了两次确定，仓库里却还留着。 */
    clearLocal();
    stubFetch(REMOTE);
    const okE5 = (await ghPush(false, true)) === true;
    const okE6 = PUTS.length === 1;

    check("空库不会清空仓库", okE1 && okE2 && okE3 && okE4 && okE5 && okE6,
          "被拦=" + okE1 + " 没发请求=" + okE2 + " 有数据照传=" + okE3 + " 只发一次=" + okE4 +
          " 明确清空放行=" + okE5 + " force 只发一次=" + okE6);

    /* ── 新设备：粘令牌 → 选仓库之后，数据得自己回来 ──
       自动回灌原本只在页面启动时跑一次，所以这条流程会停在「连上了、但一台机身都没有」，
       看着像没成功。触发点补在「选完仓库」那一刻。
       下半段用**读取次数**证明它没有越界：本机有数据时一次远端都不该读。 */
    clearGh(); ghRepos = null; clearLocal();
    saveGh({ token:PAT, login:"octocat", auto:false });
    global.fetch = ghStub(true);
    contentsGets = 0;
    __setValue("#gh-pick", "octocat/film-tap-data");
    await useGhRepo();
    const okD1 = !!load().cameras.x1;
    const okD2 = contentsGets === 1;

    /* 本机已经有数据 → 这次连接绝不能被远端覆盖掉 */
    save({ version:2, films:{},
           cameras:{ mine:{ id:"mine", name:"本机自己的", format:"135", loaded:null, history:[] } } });
    saveGh({ owner:"octocat", repo:"film-tap-data", token:PAT,
             path:"data.json", branch:"main", auto:false });
    global.fetch = ghStub(true);
    contentsGets = 0;
    await useGhRepo();
    const after = load().cameras;
    const okD3 = !!after.mine && !after.x1;
    const okD4 = contentsGets === 0;

    check("新设备选完仓库自动拉回", okD1 && okD2 && okD3 && okD4,
          "拉回来了=" + okD1 + " 只读一次远端=" + okD2 +
          " 有数据不覆盖=" + okD3 + " 有数据不读远端=" + okD4);

    /* ══════════════════════════════════════════════════════════
       WebDAV 的三层诊断。
       网络不通、被跨域拦、密码不对 —— 这三种在浏览器里抛的都是
       同一个 TypeError（Failed to fetch），所以旧文案
       「连不上 NAS，检查地址和网络」**三种情况都会出现**，
       用户于是跑去查一个根本没坏的网络（用户的实际场景：外网走 IPv6 DDNS
       域名，网络本身是通的，问题只可能在跨域或证书）。
       testDav 用一次 no-cors 探测把它们分开。
       ⚠️ 这个文件是 String.raw 模板串，注释里**不能用反引号**，会截断驱动。
       ══════════════════════════════════════════════════════════ */
    __setLoc("", "#/settings"); render();
    __setValue("#dav-url",  "https://nas.example.com/dav/film/");
    __setValue("#dav-user", "filmapp");
    __setValue("#dav-pass", "app-password");

    const probeOK = async (url, opt) => {
      if (opt && opt.mode === "no-cors") return { ok:true, status:0, type:"opaque" };
      throw new TypeError("Failed to fetch");
    };

    /* ① 连 no-cors 探测都抛 → 网络层就不通 */
    global.fetch = async () => { throw new TypeError("Failed to fetch"); };
    await testDav();
    const w1 = /连不到这个地址/.test(__el("#toast").textContent);

    /* ② 探测过了、正常请求被拦 → 网络好着，是 NAS 没允许跨域 */
    global.fetch = probeOK;
    await testDav();
    const w2 = /没允许跨域/.test(__el("#toast").textContent);

    /* ③ 探测过、真请求 404 → 通了，只是还没有备份文件 */
    global.fetch = async (url, opt) => {
      if (opt && opt.mode === "no-cors") return { ok:true, status:0, type:"opaque" };
      return { ok:false, status:404, json: async () => ({}) };
    };
    await testDav();
    const w3 = /还没有备份文件/.test(__el("#toast").textContent);

    /* ④ 认证失败要说成认证失败，别混进「连不上」 */
    global.fetch = async (url, opt) => {
      if (opt && opt.mode === "no-cors") return { ok:true, status:0, type:"opaque" };
      return { ok:false, status:401, json: async () => ({}) };
    };
    await testDav();
    const w4 = /账号密码不对/.test(__el("#toast").textContent);

    check("WebDAV 诊断分得清三种失败", w1 && w2 && w3 && w4,
          "网络不通=" + w1 + " 跨域被拦=" + w2 + " 无备份=" + w3 + " 密码错=" + w4);
  } catch (e) {
    check("私有仓库同步", false, "抛异常 " + e.message);
  }

  __report(REPORT, pass, fail);
})();
`;

global.__setLoc = (search, hash) => { loc.search = search; loc.hash = hash; };
global.__app = () => el("#app").innerHTML;
global.__setValue = (sel, v) => { el(sel).value = v; };
/* 读回桩元素的属性（disabled / style 之类）。只给 value 一个 setter 是不够的：
   「按钮该不该亮」这件事落在 disabled 上，光看 innerHTML 里的字符串
   证明不了按下之后它真的变了。 */
global.__el = sel => el(sel);
global.__report = (rows, pass, fail) => {
  console.log("检查项".padEnd(20) + "结果".padEnd(10) + "备注");
  console.log("-".repeat(88));
  for (const [name, st, note] of rows) console.log(name.padEnd(20) + st.padEnd(12) + note);
  console.log("-".repeat(88));
  console.log(`通过 ${pass} / 失败 ${fail}`);
  process.exitCode = fail ? 1 : 0;
};

try {
  /* 先用 vm.Script 带文件名编译一次。
     eval 抛语法错误时不给行号，而这个驱动有两万多字符 ——
     没有行号基本没法定位，只能靠猜。带着文件名编译，报错就会指到 driver.js:NNN。 */
  new (require("vm").Script)(js + driver, { filename: "driver.js" });
  eval(js + driver);
} catch (e) {
  console.error("脚本执行失败:", e.message);
  if (e.stack) console.error(e.stack.split("\n").slice(0, 6).join("\n"));
  process.exit(1);
}
