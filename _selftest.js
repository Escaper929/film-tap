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
  /* GitHub 令牌的形状。项目现在已经**不用**任何 GitHub 令牌了（数据只在
     自己的 NAS 上），所以这一条从「头号风险」降级成了纵深防御 ——
     留着是为了拦住「哪天有人图省事又往源码里贴一枚」。
     ghp_ = classic PAT，gho_ = OAuth，ghs_ = 服务令牌，github_pat_ = fine-grained。 */
  [/\bghp_[A-Za-z0-9]{20,}/,                               "硬编码的 GitHub classic 令牌"],
  [/\bgho_[A-Za-z0-9]{20,}/,                               "硬编码的 GitHub OAuth 令牌"],
  [/\bghs_[A-Za-z0-9]{20,}/,                               "硬编码的 GitHub 服务令牌"],
  [/\bgithub_pat_[A-Za-z0-9_]{20,}/,                       "硬编码的 GitHub 细粒度令牌"]
];

/* 护栏要覆盖仓库里所有发得出去的文本文件。
   关键：**暴露面是「仓库可见范围」，不是「站点可访问范围」**。
   上次的泄露就发生在 _selftest.js 里 —— 那时候这个文件不在站点上（GitHub Pages
   会跳过下划线开头的文件），但它在公开仓库里照样能拿到。

   现在这个仓库连站点都不是了（页面只从 NAS 上发），可规则没变：**公开仓库里
   的每个文本文件都是公开的**。所以新加的脚本、工作流、Dockerfile 都要加进来 ——
   没加进来就等于没护栏，这个坑踩过。

   nas/ 那几项尤其要留着：部署脚本最容易顺手把 NAS 的内网地址和密码写成字面量，
   而那正是这个护栏要拦的东西。所以 deploy.sh 只接收「主机」当参数，自己不含地址。 */
const SHIPPED = [
  "index.html", "sw.js", "manifest.webmanifest",
  "README.md", "_selftest.js", "_shots.py", "_firstrun.py",
  "model/build_fob.py", "model/fob.scad", "model/preview.py", "model/render.py",
  "nas/server.py", "nas/deploy.sh", "nas/update.sh",
  "Dockerfile", ".dockerignore", ".github/workflows/docker.yml"
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
                              // 真元素上一定有的方法。桩缺了它们，
                              // 「密码框出错时把光标送回去」这种代码就没法测。
                              focus(){}, select(){},
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
    must:["导出备份","导入备份","已登记的机身 ID","?c=","清空全部数据",
          "同步到这台 NAS",
          "同步到 WebDAV","WebDAV 目录地址","保存同步设置"],
    /* GitHub 那整套已经拆掉了。下面头几个 mustNot 是**防它偷偷长回来**：
       页面里再冒出任何一个 GitHub 相关的字眼，都说明有残留没摘干净。
       （后面几条是凭据形状的老护栏，留着。） */
    mustNot:["GitHub","github","私有仓库","访问令牌","fine-grained",
             "192.168.","WEBDAV_AUTH","Authorization: Basic","Bearer ",
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
    "saveDavFromForm","syncToNas","restoreFromNas","testDav","clearDavSettings",
    /* 自建 NAS 那一栏的按钮。这张表必须跟着渲染出来的 onclick 一起长 ——
       名字漏了一个，被测的那段 JS 就会去全局找真的实现而找不到，于是报「有泄漏」。
       那个失败看着像注入，其实是这张表过期了，纯噪音。 */
    "nasLogin","nasLogout","nasPush","nasPull","toggleNasAuto","syncNasLoginBtn",
    "wipe","exportCorrupt",
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
     这两条路上的数据可能来自导入的 JSON 或从 NAS 拉回来的远端数据，不经过 parseRoute */
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

  /* 设置页还有几种状态平时渲染不到，得单独扫：
     NAS 那一栏有四种排版（还在探 / 不是从 NAS 打开 / 探到了没登录 / 已登录），
     每种多出来的 onclick 都得过一遍 —— 这一栏里带着用户填的密码框，
     多一条能注入的路径就是多一个把密码送出去的口子。
     ⚠️ 扫完必须把 nasState/nasSess 还原 —— 后面「回灌只在空库发生」那些
        用例依赖「此刻到底在不在 NAS 上」，留一个假状态进去会让它们测出假结果。 */
  const keepNasState = nasState, keepNasSess = nasSess;
  const STATES = [
    { note:"NAS 探到了、未登录", st:"yes", sess:false },
    { note:"NAS 已登录",        st:"yes", sess:true  },
    { note:"不是从 NAS 打开",   st:"no",  sess:false }
  ];
  for (const st of STATES){
    nasState = st.st; nasSess = st.sess;
    __setLoc("", "#/settings");
    try { render(); } catch (e){ bad.push("#/settings(" + st.note + ",抛异常)"); continue; }
    if (runHandlers(__app()).leak) bad.push("#/settings(" + st.note + ")");
  }
  nasState = keepNasState; nasSess = keepNasSess;

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
   同步层的通用行为 —— 和具体走哪条路无关，所以放在异步段里用打桩的 fetch 跑。
   钉两件事：① 整库快照，新字段只要做成 DB 顶层字段就自动跟着走；
   ② 「只给要发出去的那一份盖时间戳」，不回写本机。
   ② 修的是一个真实故障：点一次别的同步按钮，却顺带动了另一处存储。
   ══════════════════════════════════════════════════════════ */
(async () => {
  try {
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

    /* ── 点「同步到 NAS」（WebDAV 那条路）不该顺带把数据推给自建后端 ──
       早先 syncToNas() 先把 savedAt 写回本机再 save()，而 save() 在「自动同步」
       开着时会排一次上传 —— 用户点的是 WebDAV 那个按钮，动到的却是另一处存储。

       ⚠️ 不能靠 await flushNasPush() 来收尾：这个自检把 setTimeout 桩成了 () => 0，
          于是 flush 里的「有没有排队」永远为假，测试会**假装通过**。
          所以这里自己把窗口里排上的回调全抓下来跑掉 —— 真排了上传的话，
          这一跑就会打到 /api/data。
          ⚠️ 不能只看「抓到回调没有」：toast() 自己也用 setTimeout，
             抓到它纯属正常。判定必须落到「那个 PUT 去了哪个路径」。 */
    const realSetTimeout = global.setTimeout;
    const PUTS = [];
    global.fetch = async (url, opt) => {
      const method = ((opt && opt.method) || "GET").toUpperCase();
      const u = String(url);
      if (method === "PUT"){
        PUTS.push({ url:u, body:JSON.parse(opt.body) });
        if (u.indexOf("/api/") >= 0)
          return { ok:true, status:200, json: async () => ({ ok:true, rev:"rev-x" }) };
        return { ok:true, status:201, json: async () => ({}) };
      }
      return { ok:true, status:200, json: async () => ({ data:null, rev:null }) };
    };

    saveDav({ url:"https://nas.example.com/dav/film/", user:"u", pass:"p" });
    /* 自建后端那一路的自动同步得是**开着**的 —— 关着的话「没上传」什么也证明不了。 */
    saveNas({ auto:true });
    nasState = "yes"; nasSess = true;

    const pending = [];
    global.setTimeout = (fn) => { pending.push(fn); return 1; };
    await syncToNas();
    global.setTimeout = realSetTimeout;
    for (const fn of pending){ try{ await fn(); }catch(e){} }

    const wentDav = PUTS.some(p => p.url.indexOf("nas.example.com") >= 0);
    const wentApi = PUTS.filter(p => p.url.indexOf("/api/") >= 0).length;
    const okN1 = wentDav && wentApi === 0;
    const okN2 = load().savedAt == null;          // savedAt 也不该被写进本机
    check("WebDAV 同步不触达自建 NAS", okN1 && okN2,
          "写到 WebDAV=" + wentDav + " 写 /api/ 次数=" + wentApi + " 本机无 savedAt=" + okN2);

    /* 后面 NAS 那批用例自己会摆状态，这里把它们还原成「不在 NAS 上」。 */
    clearNasRev();
    nasState = "no"; nasSess = false;

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

    /* ① 连 no-cors 探测都抛 → 连接根本建不起来。
       这一层的文案必须同时点到**证书**：自签证书在 TLS 阶段就被拒，
       抛的异常和「DNS 解析不了」一模一样。飞牛 / 群晖自带的 WebDAV
       默认就是自签证书 —— 漏了这两个字，用户就去查一个没坏的域名了。 */
    global.fetch = async () => { throw new TypeError("Failed to fetch"); };
    await testDav();
    const t1 = __el("#toast").textContent;
    const w1 = /连不到这个地址/.test(t1);
    const w5 = /证书/.test(t1);

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

    check("WebDAV 诊断分得清四种失败", w1 && w2 && w3 && w4 && w5,
          "网络/证书不通=" + w1 + " 提到证书=" + w5 + " 跨域被拦=" + w2 +
          " 无备份=" + w3 + " 密码错=" + w4);
  } catch (e) {
    check("同步层通用行为", false, "抛异常 " + e.message);
  }

  /* ══════════════════════════════════════════════════════════
     自建 NAS 后端（同源 /api/*）。
     这条路和上面两条最大的不同只有一处，但结果差很远：
     **登录态住在服务器下发的 cookie 里，而不是住在 localStorage 里。**
     浏览器「闲置 7 天清空存储」清的是脚本能写的那些（localStorage /
     IndexedDB / Cache API / document.cookie）；Set-Cookie 下发的 cookie
     不在清理范围内 —— 所以「本机被清空后自动恢复」这次是真的。
     自检钉的就是这条链子上的每一环，外加一条独立规矩：**密码绝不许落盘**。
     ══════════════════════════════════════════════════════════ */
  await (async () => {
    try {
      const NST = { sess:false, data:null, rev:null, puts:0, reject:null, noApp:false };
      const NCALLS = [];
      const NR = (obj, code) => ({ ok: code >= 200 && code < 300, status: code,
                                   json: async () => obj });
      /* 密码运行时拼出来，不在文件里留下一个可命中的字面量 ——
         这个文件本身就在护栏的扫描范围内。 */
      const NPW = "correct" + "-horse-" + "battery";

      global.fetch = async (url, opt) => {
        const path = String(url), method = ((opt && opt.method) || "GET").toUpperCase();
        NCALLS.push({ path, method, opt: opt || {} });

        if (path === "/api/health")
          return NST.noApp ? NR({ app:"somebody-else" }, 200)
                           : NR({ app:"film-tap", ok:true, configured:true }, 200);
        if (path === "/api/session")
          /* 注意：真实后端这里回的是 200 + {ok:false}，不是 401。
             「现在是谁」的答案可以是「没人」，回 401 的话浏览器会把每次
             未登录打开页面都记成一条 console error。 */
          return NR({ ok:NST.sess }, 200);
        if (path === "/api/login"){
          if (JSON.parse(opt.body).password !== NPW)
            return NR({ error:"bad_password", message:"密码不对" }, 401);
          NST.sess = true;
          return NR({ ok:true }, 200);
        }
        if (path === "/api/data" && method === "GET"){
          if (!NST.sess) return NR({ error:"unauthorized" }, 401);
          return NR({ data:NST.data, rev:NST.rev }, 200);
        }
        if (path === "/api/data" && method === "PUT"){
          if (!NST.sess) return NR({ error:"unauthorized" }, 401);
          if (NST.reject === "stale")
            return NR({ error:"stale", rev:NST.rev,
                        message:"数据在别处被改过，先拉一次再传" }, 409);
          if (NST.reject === "empty")
            return NR({ error:"not_empty_overwrite", remote:3,
                        message:"服务端有 3 台机身，这份是空的 —— 先拉一次对齐" }, 409);
          NST.data = JSON.parse(opt.body).data;
          NST.rev  = "rev-next"; NST.puts++;
          return NR({ ok:true, rev:NST.rev }, 200);
        }
        return NR({ error:"not found" }, 404);
      };

      /* ── ① 探活认的是后端**自报的身份**，不是「这个路径返回了 200」 ──
         别的服务恰好也有 /api/health 时，不能把它的应答当成我们的后端。 */
      nasState = "unknown";
      NST.noApp = true;  const other = await nasHealth();
      NST.noApp = false; const mine  = await nasHealth();
      check("NAS：探活认自报身份",
            other === null && !!mine && mine.app === "film-tap",
            "别人的 /api/health 不认=" + (other === null) + " 自己的认=" + !!mine);

      /* ── ② 「密码错」必须和「连不上」分开说 ──
         混成一句「连不上」的话，用户会跑去查一个根本没坏的网络 ——
         这个项目上一轮就是在修这类误判。 */
      localStorage.removeItem(NAS_KEY);
      nasSess = false; nasState = "yes";
      __setValue("#nas-pass", "wrong-" + "password");
      await nasLogin();
      const tBad = __el("#toast").textContent;
      const saysWrong  = /密码不对/.test(tBad);
      const notNetwork = !/连不上/.test(tBad);

      /* ── ③ 密码绝不许落盘 ── */
      __setValue("#nas-pass", NPW);
      await nasLogin();
      const dumped = JSON.stringify(localStorage.getItem(NAS_KEY) || "");
      const noPwOnDisk = dumped.indexOf(NPW) < 0;
      const sessOn     = nasSess === true;
      const boxCleared = __el("#nas-pass").value === "";
      check("NAS：密码错≠连不上，也不落盘",
            saysWrong && notNetwork && noPwOnDisk && sessOn && boxCleared,
            "说密码不对=" + saysWrong + " 没混成网络问题=" + notNetwork +
            " 本机不存密码=" + noPwOnDisk + " 会话建立=" + sessOn + " 输完即清空=" + boxCleared);

      /* ── ④ 请求必须是**同源相对路径**，且带上 cookie ──
         路径里写死域名的话，换个域名（或从局域网直连）就整条断掉；
         credentials 不是 same-origin 的话，服务器下发的会话根本发不回去。 */
      const bad = NCALLS.filter(c =>
        c.path.indexOf("/api/") !== 0 || c.opt.credentials !== "same-origin");
      check("NAS：请求同源且带 cookie",
            NCALLS.length > 0 && bad.length === 0,
            bad.length ? ("有 " + bad.length + " 个请求不对：" + bad[0].path)
                       : (NCALLS.length + " 个请求全部是 /api/ 相对路径 + same-origin"));

      /* ── ⑤ 回灌的两条硬规矩：只在**本机为空**时动手，本机有数据一个请求都不发 ── */
      NST.data = { version:2, cameras:{
        r1:{ id:"r1", name:"NAS 上的机身 禄来 3.5F", format:"120", loaded:null, history:[] }
      }, films:{} };
      NST.rev = "rev-1";
      localStorage.removeItem("filmtap.v1");
      localStorage.removeItem(NAS_KEY);

      NCALLS.length = 0;
      const back = await nasRehydrate();
      const gotBack = Object.keys(load().cameras).length === 1 &&
                      load().cameras.r1.name === "NAS 上的机身 禄来 3.5F";

      NCALLS.length = 0;
      const again = await nasRehydrate();          // 本机已有数据
      check("NAS：回灌仅在本机为空时",
            back === true && gotBack && again === false && NCALLS.length === 0,
            "拉回来=" + gotBack + " 本机有数据时不请求=" + (NCALLS.length === 0));

      /* ── ⑥ 上传：带上「我上次读到的版本」，服务端据此判断别处有没有改过 ── */
      NCALLS.length = 0;
      NST.puts = 0;
      await nasPush(false);
      const put = NCALLS.find(c => c.method === "PUT");
      const sentRev = put ? JSON.parse(put.opt.body).rev : "<无请求>";
      const sentSavedAt = put ? !!JSON.parse(put.opt.body).data.savedAt : false;
      const localClean  = load().savedAt === undefined;   // savedAt 不能回写本机，否则 save→上传→save 成环
      check("NAS：上传带版本号",
            NST.puts === 1 && sentRev === "rev-1" && sentSavedAt && localClean,
            "发了一次=" + (NST.puts === 1) + " 带 rev=" + sentRev +
            " 带 savedAt=" + sentSavedAt + " 本机不落 savedAt=" + localClean);

      /* ── ⑦ 两种 409 都要说出来，不能吃掉 ── */
      NST.reject = "stale";   await nasPush(true);
      const tStale = __el("#toast").textContent;
      NST.reject = "empty";   await nasPush(true);
      const tEmpty = __el("#toast").textContent;
      NST.reject = null;
      check("NAS：两种 409 都拦下并说明",
            /别处被改过/.test(tStale) && /3 台机身/.test(tEmpty),
            "别处改过=" + /别处被改过/.test(tStale) + " 会被清空时说清台数=" + /3 台机身/.test(tEmpty));

      /* ── ⑧ 会话失效：**不弹提示**，让界面自己退回未登录那一屏 ──
         它是自动同步那条路上的失败，每次都弹的话会一边写一边刷屏。
         ⚠️ 这里要让「本机以为还登录着、服务端已经不认了」——
         只把服务端那边关掉，否则 nasPush 会在发请求之前就返回，
         那条 401 分支根本走不到。 */
      nasSess = true; NST.sess = false;
      __el("#toast").textContent = "哨兵";
      await nasPush(true);
      const quietOn401 = __el("#toast").textContent === "哨兵" && nasSess === false;
      check("NAS：会话失效不刷屏",
            quietOn401, "没弹提示=" + (__el("#toast").textContent === "哨兵") +
                        " 会话标记已清=" + (nasSess === false));

      /* ── ⑨ 这是整条路的重点：**本机被清空之后，用户一步都不用做** ──
         模拟 Safari 闲置清理：数据和一些本机偏好都没了，
         但会话是服务器下发的 cookie —— 它不在那次清理范围内，还在。
         重跑一次启动路径，数据应该自己回来。 */
      NST.sess = true;
      NST.data = { version:2, cameras:{
        r1:{ id:"r1", name:"NAS 上的机身 禄来 3.5F", format:"120", loaded:null, history:[] },
        r2:{ id:"r2", name:"NAS 上的机身 Leica M6",   format:"135", loaded:null, history:[] }
      }, films:{} };
      NST.rev = "rev-2";
      localStorage.removeItem("filmtap.v1");
      localStorage.removeItem(NAS_KEY);
      nasState = "unknown"; nasSess = false;

      await nasBoot();
      const healed = Object.keys(load().cameras).length === 2;
      const saidIt = /已从 NAS 恢复 2 台机身/.test(__el("#toast").textContent);
      check("NAS：清空后自动恢复",
            healed && saidIt && nasSess === true,
            "数据自己回来=" + healed + " 提示说清台数=" + saidIt + " 会话还活着=" + (nasSess === true));

      /* ── ⑩ 设置页那一栏的排版，以及「空着就点不动」 ── */
      __setLoc("", "#/settings");
      nasState = "yes"; nasSess = false;
      render();
      let h = __app();
      const showPass = h.indexOf('id="nas-pass"') >= 0;
      const startOff = h.indexOf('id="nas-login" disabled') >= 0;
      const wired    = h.indexOf('oninput="syncNasLoginBtn()"') >= 0;

      __setValue("#nas-pass", "");
      syncNasLoginBtn();
      const emptyOff = __el("#nas-login").disabled === true;
      __setValue("#nas-pass", "   \n ");
      syncNasLoginBtn();
      const blankOff = __el("#nas-login").disabled === true;   // 纯空白不算内容
      __setValue("#nas-pass", "x");
      syncNasLoginBtn();
      const lightsUp = __el("#nas-login").disabled === false;

      nasSess = true;
      saveNas({ at:"2026-09-27T07:30:00Z" });
      render();
      h = __app();
      const loggedIn = h.indexOf("立即存到 NAS") >= 0 && h.indexOf("从 NAS 恢复") >= 0;
      const showsAt  = h.indexOf("2026-09-27 07:30 UTC") >= 0;
      const stillNoPw = h.indexOf(NPW) < 0;
      check("NAS：设置页排版与按钮",
            showPass && startOff && wired && emptyOff && blankOff && lightsUp &&
            loggedIn && showsAt && stillNoPw,
            "密码框=" + showPass + " 初始禁用=" + startOff + " 空着不亮=" + emptyOff +
            " 纯空白不算=" + blankOff + " 输了就亮=" + lightsUp +
            " 已登录排版=" + loggedIn + " 显示上次同步=" + showsAt + " 页面无密码=" + stillNoPw);

      /* ── ⑪ 不是从 NAS 打开时，整栏收起，不留半个入口 ── */
      nasState = "no"; nasSess = false;
      render();
      h = __app();
      const collapsed = h.indexOf("只在页面") >= 0 && h.indexOf('id="nas-login"') < 0 &&
                        h.indexOf('id="nas-pass"') < 0;
      check("NAS：不在 NAS 上时收起", collapsed,
            collapsed ? "只留一句说明，没有密码框和按钮" : "还留了入口");
    } catch (e) {
      check("NAS 自建后端", false, "抛异常 " + e.message);
    }
  })();

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
