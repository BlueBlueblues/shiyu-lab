/* 试玉 · 四阶段工作台导航（B 交付 · 规则补全版）
 *
 * 职责：只做**视图切换、状态呈现与交互约束**，不加载任何数据。
 *   - 数据仍由服务端渲染 + app.js 负责，避免出现第二套渲染口径；
 *   - 阶段状态**从服务端已渲染的进度条读取**（.ws-step-done / .ws-step-current / .ws-step-stale），
 *     因此不会伪造进度：后端状态变，这里就跟着变；
 *   - 不新增任何后端调用：本文件全部行为都是切 class、改文案、翻 disabled 与改点击去向。
 *
 * 四阶段与旧流程的映射（与原型一致）：
 *   识玉 = 你的想法（项目信息）
 *   琢玉 = AI 验证计划（方案 + 草稿 + 分享链接，含发布）
 *   鉴玉 = 收集真实反馈（运行日志 + 统计 + 原始回答）
 *   攻玉 = 验证报告（复盘与报告）
 *
 * 本文件实现的体验规则：
 *   R1  sy-stages 是唯一可见导航（ws-steps 由 CSS 在 sy-ready 后隐藏，但仍在 DOM 里当状态来源）；
 *       状态词只允许四种：已完成 / 进行中 / 待判定 / 待开始。
 *   R2  当前查看的阶段若已完成（done）→ 该视图进只读回看：注入 [data-testid="sy-readonly"]，
 *       禁用该视图内的写操作与其余按钮；导航类（data-sy-nav）永不禁用；提示随视图切换出现/消失。
 *   R4  每个视图最多一个主操作（.button-primary + [data-sy-primary]）：
 *         识玉/鉴玉 = 进入下一阶段（导航）；总览 = 继续任务（指向当前阶段）。
 *         琢玉 = 状态机：无方案→plan-generate；有方案未发布→experiment-publish；已发布→去鉴玉看证据。
 *         攻玉 = review-generate；已有报告时同一按钮切成「去鉴玉看证据」导航态。
 *
 * 降级纪律（R6）：脚本没跑完 / 报错时 body 上没有 sy-ready，所有区块照常显示（可读不可切换），
 * 只读提示与主操作标记也都是运行时注入，不会让页面变空白。
 */
(function () {
  "use strict";

  var VIEW_STAGE = { shiyu: 0, zhuoyu: 1, jianyu: 2, gongyu: 3 };
  var STAGE_VIEW = ["shiyu", "zhuoyu", "jianyu", "gongyu"];

  var STAGES = [
    { key: "shiyu", name: "识玉", desc: "明确待验证的假设" },
    { key: "zhuoyu", name: "琢玉", desc: "设计与发布实验" },
    { key: "jianyu", name: "鉴玉", desc: "查看证据与判定" },
    { key: "gongyu", name: "攻玉", desc: "形成下一轮策略" }
  ];

  function byTestId(id) { return document.querySelector('[data-testid="' + id + '"]'); }

  function stageGroup(key) { return document.querySelector('[data-stage="' + key + '"]'); }

  /* ---------------- 状态读取 ----------------
     唯一真实来源是服务端在 sy-stages 上渲染的 data-sy-state-<role>，
     它与四张角色卡（_agent_dashboard.html）读的是同一份 agent_tasks 判定
     （history.agent_stage_states）。这样导航与卡片不可能再各说一套。
     老版本模板没有这些属性时，退回读 ws-steps，并接受原有语义（仅在本文件兜底）。 */

  var STAGE_KEYS = ["shiyu", "zhuoyu", "jianyu", "gongyu"];

  function navStates() {
    var nav = document.querySelector("[data-testid='sy-stages']");
    var states = ["", "", "", ""];
    if (!nav) { return states; }
    var found = false;
    for (var i = 0; i < STAGE_KEYS.length; i += 1) {
      var v = nav.getAttribute("data-sy-state-" + STAGE_KEYS[i]);
      if (v) { found = true; }
      states[i] = v || "";
    }
    return found ? states : null;
  }

  function stepStates() {
    /* 退回路径：从服务端渲染的进度条读；读不到就全部空着，绝不猜。 */
    var strip = document.querySelector('[data-testid="ws-steps"]');
    var states = ["", "", "", ""];
    if (!strip) { return states; }
    var items = strip.querySelectorAll(".ws-step");
    for (var i = 0; i < items.length && i < 4; i += 1) {
      var el = items[i];
      if (el.classList.contains("ws-step-done")) { states[i] = "done"; }
      else if (el.classList.contains("ws-step-stale")) { states[i] = "waiting"; }
      else if (el.classList.contains("ws-step-current")) { states[i] = "current"; }
      else { states[i] = "pending"; }
    }
    return states;
  }

  function resolvedStates() {
    var fromNav = navStates();
    return fromNav || stepStates();
  }

  function label(state) {
    if (state === "done") { return "已完成"; }
    if (state === "current") { return "进行中"; }
    if (state === "waiting") { return "待判定"; }
    return "待开始";
  }

  function isComplete(state) { return state === "done"; }

  /* 第一个未完成的阶段；全完成时回到最后一个阶段。返回视图 key。 */
  function nextOpenView() {
    var states = resolvedStates();
    for (var i = 1; i < states.length; i += 1) {
      if (!isComplete(states[i])) { return STAGE_VIEW[i]; }
    }
    return "gongyu";
  }

  /* ---------------- 导航构建 ---------------- */

  function buildNav() {
    var nav = document.querySelector("[data-testid='sy-stages']");
    if (!nav) { return; }
    var states = resolvedStates();
    nav.innerHTML = STAGES.map(function (stage, index) {
      var state = states[index];
      var cls = "sy-stage" + (state === "done" ? " sy-done" : "") + (state === "waiting" ? " sy-waiting" : "");
      return '<button type="button" class="' + cls + '" data-sy-view="' + stage.key + '"' +
        ' aria-label="' + stage.name + '，' + label(state) + '">' +
        "<strong>" + stage.name + "</strong><span>" + label(state) + "</span></button>";
    }).join("");
  }

  /* 当前所在的阶段（第一个未完成），用于总览里的「当前阶段」与顶部提示。
     与导航、角色卡同源，不另外推导。 */
  function currentStageIndex() {
    var states = resolvedStates();
    for (var i = 0; i < states.length; i += 1) {
      if (!isComplete(states[i])) { return i; }
    }
    return STAGE_VIEW.length - 1;
  }

  function currentView() {
    var match = /#sy-([a-z]+)/.exec(window.location.hash || "");
    var key = match ? match[1] : "overview";
    var known = ["overview"].concat(STAGES.map(function (s) { return s.key; }));
    return known.indexOf(key) >= 0 ? key : "overview";
  }

  function viewName(key) {
    for (var i = 0; i < STAGES.length; i += 1) {
      if (STAGES[i].key === key) { return STAGES[i].name; }
    }
    return "总览";
  }

  function go(view) {
    window.location.hash = "sy-" + view;
    apply(view, true);   /* hashchange 也会触发，这里先应用一次保证即时 */
  }

  /* ---------------- R4：每个视图唯一主操作 ---------------- */

  /* 候选顺序 = 静态模板里的落点；JS 只把状态对应的那一个标成主操作。 */
  function primaryCandidates(view) {
    if (view === "overview") { return [byTestId("sy-continue")]; }
    if (view === "shiyu") { return [byTestId("shiyu-to-zhuoyu")]; }
    if (view === "jianyu") { return [byTestId("jianyu-to-gongyu")]; }
    if (view === "gongyu") { return [byTestId("review-generate")]; }
    if (view === "zhuoyu") {
      var published = byTestId("publish-seal") || byTestId("service-url");
      var plan = byTestId("plan-generate");
      var publish = byTestId("experiment-publish");
      var toJianyu = byTestId("zhuoyu-to-jianyu");
      /* 已发布：模板给的是「去鉴玉看证据」导航入口，没有写按钮 */
      if (published) { return [toJianyu]; }
      if (publish) { return [publish, plan]; }
      return [plan];
    }
    return [];
  }

  function elementIndex(list, el) {
    for (var i = 0; i < list.length; i += 1) { if (list[i] === el) { return i; } }
    return -1;
  }

  function applyPrimary(view) {
    var group = stageGroup(view);
    if (!group) { return; }

    var cands = primaryCandidates(view).filter(Boolean);
    var chosen = null;
    for (var i = 0; i < cands.length; i += 1) {
      if (!cands[i].disabled) { chosen = cands[i]; break; }
    }
    if (!chosen) { chosen = cands[0] || null; }

    cands.forEach(function (el) {
      var isPrimary = el === chosen;
      el.classList.toggle("button-primary", isPrimary);
      el.classList.toggle("button-ghost", !isPrimary);
      if (isPrimary) { el.setAttribute("data-sy-primary", ""); }
      else { el.removeAttribute("data-sy-primary"); }
    });

    /* 文字导航落点：继续任务 → 当前阶段；其余主操作 → 阶段序列的下一步 */
    if (view === "overview" && chosen) {
      chosen.setAttribute("data-sy-next", nextOpenView());
    }
    if (view === "jianyu") {
      var jianyuPrimary = chosen;
      if (jianyuPrimary) { jianyuPrimary.setAttribute("data-sy-next", "gongyu"); }
    }
    if (view === "zhuoyu") {
      var published = byTestId("publish-seal") || byTestId("service-url");
      var publishBtn = byTestId("experiment-publish");
      if (published && chosen) {
        chosen.setAttribute("data-sy-next", "jianyu");
        chosen.setAttribute("data-sy-nav", "");
        if (chosen.tagName === "BUTTON") {
          /* 已发布不是写操作：还原成可点的导航入口 */
          chosen.disabled = false;
          chosen.setAttribute("data-sy-keep-enabled", "");
        }
      } else if (publishBtn && chosen === publishBtn) {
        publishBtn.removeAttribute("data-sy-next");
        publishBtn.removeAttribute("data-sy-nav");
      }
    }
    if (view === "gongyu") {
      var gen = byTestId("review-generate");
      if (!gen) { return; }
      if (gen === chosen && byTestId("report-meta")) {
        /* 已有报告：主操作按钮改成「去鉴玉看证据」的导航入口，
           避免出现「文案说要重新生成、点击却只跳视图」的错位；不新增任何后端调用 */
        gen.textContent = "去鉴玉看证据";
        gen.setAttribute("data-sy-next", "jianyu");
        gen.setAttribute("data-sy-nav", "");
        gen.setAttribute("data-sy-keep-enabled", "");
        gen.disabled = false;
      } else {
        if (gen.getAttribute("data-sy-label")) {
          gen.textContent = gen.getAttribute("data-sy-label");
        }
        gen.removeAttribute("data-sy-next");
        gen.removeAttribute("data-sy-nav");
        gen.removeAttribute("data-sy-keep-enabled");
      }
    }
  }

  /* ---------------- R2：已完成阶段只读回看 ---------------- */

  function readonlyHint() {
    var el = document.querySelector('[data-testid="sy-readonly"]');
    if (!el) {
      el = document.createElement("p");
      el.className = "sy-readonly";
      el.setAttribute("data-testid", "sy-readonly");
      el.setAttribute("role", "status");
      el.textContent = "回看模式：该阶段已完成，内容仅可查看，不可修改或提交。";
    }
    return el;
  }

  function applyReadonly(view) {
    var group = stageGroup(view);
    var index = elementIndex(STAGE_VIEW, view);
    var states = resolvedStates();
    var readonly = group && index >= 0 && isComplete(states[index]);
    if (!group) { return; }
    group.classList.toggle("sy-readonly-view", Boolean(readonly));
    if (!readonly) { return; }
    group.insertBefore(readonlyHint(), group.firstChild);
    /* 只读要禁的是「会改变数据的操作」，不是「这个阶段里的所有控件」。
       之前按「除 data-sy-nav 外全禁」来判断，结果把「打开链接」「复制」「预览」
       这类纯读取与工具操作也一起禁掉了——回看一个已完成阶段时，连分享链接都打不开、
       复制不了，看起来就像功能坏了。
       现在改成白名单：只有显式标记 data-sy-write 的控件（生成方案 / 发布实验 /
       编辑草稿 / 生成复盘）会被禁用；导航、预览、打开链接、复制一律保持可用。
       注意 <a class="button"> 没有 disabled 属性，必须用 aria-disabled + 捕获阶段拦截。 */
    Array.prototype.forEach.call(group.querySelectorAll("[data-sy-write]"), function (el) {
      if (el.tagName === "A") {
        el.setAttribute("aria-disabled", "true");
        el.setAttribute("tabindex", "-1");
      } else {
        el.disabled = true;
      }
      el.setAttribute("data-sy-readonly-disabled", "");
    });
    /* 只读时兜底拦住写操作：app.js 若在异步回调里重新启用按钮，这里也不让它执行 */
    if (!group.getAttribute("data-sy-guard")) {
      group.addEventListener("click", function (event) {
        if (!event.target.closest || !event.target.closest("[data-sy-readonly-disabled]")) { return; }
        event.preventDefault();
        event.stopPropagation();
        var box = byTestId("ws-status");
        if (box) { box.textContent = "回看模式：该阶段已完成，内容仅可查看，不可修改或提交。"; }
      }, true);
      group.setAttribute("data-sy-guard", "1");
    }
  }

  /* ---------------- 视图切换 ---------------- */

  function apply(view, scroll) {
    var body = document.body;
    ["overview", "shiyu", "zhuoyu", "jianyu", "gongyu"].forEach(function (key) {
      body.classList.toggle("sy-view-" + key, key === view);
    });
    var nav = document.querySelector("[data-testid='sy-stages']");
    if (nav) {
      Array.prototype.forEach.call(nav.querySelectorAll(".sy-stage"), function (btn) {
        var isCurrent = btn.getAttribute("data-sy-view") === view;
        btn.setAttribute("aria-current", isCurrent ? "step" : "false");
      });
    }
    var back = document.querySelector("[data-sy-overview]");
    if (back) { back.hidden = view === "overview"; }
    var round = document.querySelector("[data-testid='sy-round']");
    if (round) {
      round.textContent = view === "overview" ? "工作台总览" : "当前阶段 · " + viewName(view);
    }
    var where = document.querySelector("[data-testid='sy-where-stage']");
    if (where) { where.textContent = STAGES[currentStageIndex()].name; }
    applyReadonly(view);
    applyPrimary(view);
    /* 视口只在「用户主动切视图」时回到顶部。
       后台状态刷新（app.js 每 3 秒轮询后调用 __SY_SYNC__）必须不传 scroll，
       否则用户滚到哪都会被拉回顶部——这是上一版引入的回归，这里明确堵住。 */
    if (scroll === true) {
      window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
    }
  }

  /* ---------------- 刷新同步（页面不重载也要一致） ----------------
     角色运行后 app.js 会重新取 /agent-tasks；把四个角色的最新状态映射回阶段后，
     导航重画、当前阶段与只读态一起跟着更新，不需要刷新页面。
     注意：这里改的是导航自身的 data-sy-state-* 属性，DOM 里 sy-stages 的
     后代节点会被重建，所以 attributes 只写这一个元素、不碰它的 children。 */

  var CARD_TO_STAGE = { done: "done", running: "current", awaiting_confirmation: "waiting", failed: "waiting", rejected: "waiting", awaiting_build: "current", awaiting_publish: "current", inherited: "done", pending: "pending" };

  function syncAgentStates(map) {
    if (!map) { return false; }
    var nav = document.querySelector("[data-testid='sy-stages']");
    var applied = false;
    STAGE_KEYS.forEach(function (role) {
      var raw = map[role];
      if (raw === undefined || raw === null) { return; }
      var next = CARD_TO_STAGE[raw] || raw;
      if (nav) { nav.setAttribute("data-sy-state-" + role, next); }
      applied = true;
    });
    if (!applied) { return false; }
    buildNav();
    /* 只更新状态，不动视口：本函数会被后台轮询反复调用。 */
    apply(currentView(), false);
    return true;
  }

  /* app.js 取到新状态后调用这一个入口，避免两套渲染口径。 */
  window.__SY_SYNC__ = function (map) {
    try { return syncAgentStates(map); } catch (error) { return false; }
  };

  /* 兜底：模板没带 data-sy-state-* 时，至少把卡片上的状态词翻译过来。 */
  function syncFromCards() {
    var cards = document.querySelectorAll("[data-agent-state]");
    if (!cards.length) { return false; }
    var map = {};
    Array.prototype.forEach.call(cards, function (el) {
      map[el.getAttribute("data-agent-state")] = el.getAttribute("data-agent-state-value") || "pending";
    });
    return syncAgentStates(map);
  }

  /* ---------------- 启动 ---------------- */

  function init() {
    /* 标记 JS 已就绪：CSS 只有看到这个类才隐藏非当前阶段与 ws-steps，
       脚本失败时页面仍完整可读（降级而非空白）。 */
    document.body.classList.add("sy-ready");
    buildNav();

    /* 记住复盘按钮的服务端文案，用于在导航态与写操作态之间往返时还原 */
    var gen = byTestId("review-generate");
    if (gen && !gen.getAttribute("data-sy-label")) {
      gen.setAttribute("data-sy-label", gen.textContent.trim());
    }

    document.addEventListener("click", function (event) {
      var target = event.target;
      if (!target.closest) { return; }
      /* 主操作 / 导航按钮：带 data-sy-next 的走视图切换，其余交给 app.js 的既有行为 */
      var next = target.closest("[data-sy-next]");
      if (next) {
        var key = next.getAttribute("data-sy-next");
        if (key === "shiyu" || key === "zhuoyu" || key === "jianyu" || key === "gongyu") {
          event.preventDefault();
          go(key);
          return;
        }
      }
      var btn = target.closest("[data-sy-view]");
      if (btn) { go(btn.getAttribute("data-sy-view")); return; }
      var back = target.closest("[data-sy-overview]");
      if (back) { go("overview"); }
    });

    /* 导航态按钮被 app.js 的异步状态刷新再次禁用时，仍然要能点（只拦禁用，不改写操作行为） */
    document.addEventListener("click", function (event) {
      var target = event.target;
      if (!target.closest) { return; }
      var nav = target.closest("[data-sy-keep-enabled]");
      if (nav && nav.disabled) { nav.disabled = false; }
    }, true);

    /* hash 变化只可能来自用户操作（点导航、点主操作、浏览器前进后退），
       所以这里允许回顶部；后台轮询走 __SY_SYNC__，不经过这个入口。 */
    window.addEventListener("hashchange", function () { apply(currentView(), true); });

    /* app.js 每次刷新角色状态时也会调用 __SY_SYNC__，这里只做一次首屏兜底。 */
    if (!navStates()) { syncFromCards(); }

    var hash = "" + window.location.hash;
    apply(/(?:^|#)sy-[a-z]+$/.test(hash) ? currentView() : "overview", true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
