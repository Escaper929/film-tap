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
  "README.md", "_selftest.js", "_shots.py",
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

/* fetch 默认不打桩：万一有代码在启动路径上真的想发请求，会立刻炸出来，
   而不是被静默吞掉。需要网络的用例自己在驱动里设 global.fetch。 */
global.fetch = () => Promise.reject(new Error("测试里没有打桩 fetch"));

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
          "同步到自己的 NAS","WebDAV 目录地址","保存同步设置",
          "同步到私有仓库","访问令牌","保存仓库设置"],
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
     "Invalid or unexpected token"。写成 \\r 外层先还原成一个反斜杠，
     驱动再把它当转义解释，才是我们想要的 CRLF。正则里的 \d 同理，要写 \\d。 */
  const csv = '\\uFEFF型号,数量,画幅,备注\\r\\n'
            + 'Kodak Portra 400,12,135,"冰箱冷藏, 别晒"\\r\\n'
            + '"Fuji, 分装",3,120,""\\r\\n\\r\\n';
  const rows = parseDelimited(csv, sniffDelim(csv));

  const ok1 = rows.length === 3;                                          // 尾部空行被丢掉
  const ok2 = rows[0].join("|") === "型号|数量|画幅|备注";                  // BOM 不会黏在表头上
  const ok3 = rows[1][3] === "冰箱冷藏, 别晒";                             // 引号里带分隔符
  const ok4 = rows[2][0] === "Fuji, 分装";                                 // 型号本身带逗号
  const ok5 = sniffDelim("a\\tb\\tc\\n1\\t2\\t3") === "\\t";                // 从 Excel 粘贴是制表符
  const ok6 = sniffDelim("a;b;c\\n1;2;3") === ";";                        // 欧洲区 Excel 用分号
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
  const ok8 = /^\\d{4}-\\d{2}$/.test(normMonth("46000"));         // Excel 存的日期序列号
  const ok9 = normMonth("不知道") === "" && normMonth("") === "";  // 看不懂就不猜

  check("字段归一化", ok1 && ok2 && ok3 && ok4 && ok5 && ok6 && ok7 && ok8 && ok9,
        "型号带120=" + ok1 + " 显式优先=" + ok2 + " 3200不误判=" + ok3 + " 大画幅=" + ok4 +
        " 月份=" + ok5 + " 日期=" + ok6 + " 数字=" + ok7 + " 序列号=" + ok8 + " 不乱猜=" + ok9);
} catch (e) { check("字段归一化", false, "抛异常 " + e.message); }

/* ── 导入：覆盖 / 累加 / 不抹掉表里没有的字段 ── */
try {
  save(demoData());
  const rows = parseDelimited(
    "型号,数量,画幅,有效期\\n" +
    "Kodak Portra 400,20,135,2028-01\\n" +      // 已存在 → 覆盖成 20
    "Kodak Ektar 100,7,135,\\n" +               // 已存在 → 覆盖成 7
    "Lomography Color 100,5,135,2028-09\\n" +   // 新增
    ",3,135,\\n",                               // 没型号 → 跳过
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
  const u8 = new TextEncoder().encode("\\uFEFF型号,数量\\nIlford HP5 Plus 400,9\\n");
  const r2 = parseDelimited(decodeBytes(u8.buffer), ",");
  const ok3 = r2[0][0] === "型号" && r2[1][1] === "9";

  check("编码兜底（GBK）", ok1 && ok2 && ok3,
        "GBK 认出来=" + ok1 + " 行对=" + ok2 + " UTF-8+BOM=" + ok3);
} catch (e) { check("编码兜底（GBK）", false, "抛异常 " + e.message); }

/* ── 装卷扣库存 ── */
try {
  save(demoData());
  __setLoc("", "#/c/m6/load"); render();
  __setValue("#f-stock", "Ilford HP5 Plus 400");
  __setValue("#f-iso", "400");
  __setValue("#f-ei", "");
  __setValue("#f-shots", "0");
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
  __setValue("#f-shots", "0");
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
    "还剩 12 张"
  ];
  const caught = BAD_SAMPLES.every(s => CRED_RULES.some(([re]) => re.test(s)));
  const quiet  = GOOD_SAMPLES.every(s => !CRED_RULES.some(([re]) => re.test(s)));
  check("凭据护栏有效", caught && quiet,
        "能拦截 " + BAD_SAMPLES.filter(s => CRED_RULES.some(([re]) => re.test(s))).length +
        "/" + BAD_SAMPLES.length + " 不误报=" + quiet);
} catch (e) { check("凭据护栏有效", false, "抛异常 " + e.message); }

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

    clearGh();
    const okC = getGh() === null;
    check("仓库设置可清除", okC, "清掉=" + okC);
  } catch (e) {
    check("私有仓库同步", false, "抛异常 " + e.message);
  }

  __report(REPORT, pass, fail);
})();
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
