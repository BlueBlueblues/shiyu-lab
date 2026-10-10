/* FirstOrder Lab 前端脚本
 *
 * 职责（成员 B 指南 §4 B01/B04/B05）：
 *   1. 统一 JSON 请求封装：读取 {data} 或 {error{code,message,fields}}
 *   2. 管理写请求自动带同源凭据与 X-Requested-With: FirstOrderLab
 *   3. 草稿编辑页：收集完整 {landing, questions} 并 PATCH，只允许文本变化
 *   4. 问卷页：三类题收集、必答校验、UUID 幂等提交、成功/失败反馈、草稿预览禁用
 *
 * 安全：不使用 innerHTML 拼接外部文本；全部用 textContent。
 */
(function () {
  "use strict";

  var SITE_HEADER = "FirstOrderLab";
  var ADMIN_TIMEOUT_MS = 70000;   // 管理写入预算（总纲 §5：单次操作总预算 60 秒，留余量）
  var PUBLIC_TIMEOUT_MS = 30000;  // 公开提交预算

  /* ------------------------------------------------------------------ *
   * 工具
   * ------------------------------------------------------------------ */

  function uuid() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    // 兜底：非安全上下文（如 http://192.168.x.x）下 randomUUID 不可用
    var bytes = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    var hex = [];
    for (var i = 0; i < 16; i += 1) {
      hex.push((bytes[i] + 0x100).toString(16).slice(1));
    }
    return (
      hex.slice(0, 4).join("") + "-" + hex.slice(4, 6).join("") + "-" +
      hex.slice(6, 8).join("") + "-" + hex.slice(8, 10).join("") + "-" +
      hex.slice(10, 16).join("")
    );
  }

  function setStatus(element, text, kind) {
    if (!element) { return; }
    element.textContent = text || "";
    element.classList.remove("is-error", "is-ok");
    if (kind === "error") { element.classList.add("is-error"); }
    if (kind === "ok") { element.classList.add("is-ok"); }
  }

  function showFieldErrors(container, error) {
    if (!container) { return; }
    while (container.firstChild) { container.removeChild(container.firstChild); }
    var fields = (error && error.fields) || {};
    var keys = Object.keys(fields);
    if (!keys.length) {
      container.hidden = true;
      return;
    }
    var title = document.createElement("p");
    title.textContent = "请修正以下内容：";
    container.appendChild(title);
    var list = document.createElement("ul");
    keys.forEach(function (key) {
      var item = document.createElement("li");
      item.textContent = key + "：" + fields[key];
      list.appendChild(item);
    });
    container.appendChild(list);
    container.hidden = false;
  }

  function withTimeout(ms) {
    var controller = new AbortController();
    var timer = window.setTimeout(function () { controller.abort(); }, ms);
    return { signal: controller.signal, done: function () { window.clearTimeout(timer); } };
  }

  /**
   * 统一请求。成功返回 data；失败抛出 {code, message, fields, status}。
   * 管理写入自动附加同源检查所需的请求头。
   */
  function request(method, url, payload, options) {
    var opts = options || {};
    var limits = withTimeout(opts.timeout || PUBLIC_TIMEOUT_MS);
    var headers = { Accept: "application/json" };
    var init = {
      method: method,
      credentials: opts.admin ? "same-origin" : "omit",
      headers: headers,
      signal: limits.signal
    };
    if (payload !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(payload);
    }
    if (opts.admin) {
      headers["X-Requested-With"] = SITE_HEADER;
    }
    return fetch(url, init).then(function (response) {
      limits.done();
      return response.text().then(function (text) {
        var body = null;
        if (text) {
          try { body = JSON.parse(text); } catch (error) { body = null; }
        }
        if (body && body.error) {
          throw {
            status: response.status,
            code: body.error.code || "INTERNAL_ERROR",
            message: body.error.message || "请求未成功。",
            fields: body.error.fields || {},
            run_id: body.error.run_id
          };
        }
        if (!response.ok) {
          throw {
            status: response.status,
            code: "HTTP_" + response.status,
            message: "请求未成功（HTTP " + response.status + "）。",
            fields: {}
          };
        }
        return body && Object.prototype.hasOwnProperty.call(body, "data") ? body.data : body;
      });
    }).catch(function (error) {
      limits.done();
      if (error && error.code) { throw error; }
      if (error && error.name === "AbortError") {
        throw { status: 0, code: "TIMEOUT", message: "请求超时，请稍后手动重试。", fields: {} };
      }
      throw { status: 0, code: "NETWORK_ERROR", message: "网络请求失败，请检查网络后重试。", fields: {} };
    });
  }

  function messageFor(error) {
    var map = {
      AUTH_REQUIRED: "登录状态已失效，请重新登录管理端。",
      FORBIDDEN: "该操作必须从受控的管理页面发起。",
      NOT_FOUND: "资源不存在或已被删除。",
      RUN_IN_PROGRESS: "该项目已有任务执行中，请稍后再试。",
      EXPERIMENT_LOCKED: "实验已发布，内容已锁定。若要改题请创建下一轮项目。",
      IDEMPOTENCY_CONFLICT: "本次提交编号已用于不同内容，请刷新页面后重新填写。",
      VALIDATION_ERROR: "填写内容不符合要求，请检查后重试。",
      LLM_UPSTREAM_ERROR: "模型服务暂时不可用，请稍后手动重试。",
      LLM_TIMEOUT: "模型响应超时，请手动重试。",
      LLM_OUTPUT_INVALID: "模型输出不符合要求，本次未生成结果，请手动重试。",
      INTERNAL_ERROR: "服务暂时无法完成请求，请稍后重试。",
      TIMEOUT: "请求超时，请稍后手动重试。",
      NETWORK_ERROR: "网络请求失败，请检查网络后重试。"
    };
    return map[error && error.code] || (error && error.message) || "请求未成功。";
  }

  /* ------------------------------------------------------------------ *
   * 草稿编辑页（experiment_edit.html）
   * ------------------------------------------------------------------ */

  function valueOf(form, name) {
    var field = form.elements[name];
    if (!field) { return ""; }
    if (typeof field.value === "string") { return field.value; }
    return field[0] ? field[0].value : "";
  }

  function collectLanding(form) {
    var features = valueOf(form, "features")
      .split("\n")
      .map(function (line) { return line.trim(); })
      .filter(function (line) { return line.length > 0; });

    return {
      title: valueOf(form, "title").trim(),
      summary: valueOf(form, "summary").trim(),
      target_users: valueOf(form, "target_users").trim(),
      value_proposition: valueOf(form, "value_proposition").trim(),
      features: features,
      disclaimer: valueOf(form, "disclaimer").trim()
    };
  }

  function collectQuestions(form, existing) {
    // 结构（id/type/options 的编号与顺序）严格沿用服务端返回，只替换可编辑文本
    return existing.map(function (question) {
      var titleField = form.elements["question-" + question.id + "-title"];
      var requiredField = form.elements["question-" + question.id + "-required"];
      return {
        id: question.id,
        type: question.type,
        title: titleField ? titleField.value.trim() : question.title,
        required: requiredField ? !!requiredField.checked : question.required,
        options: (question.options || []).map(function (option) {
          var labelField = form.elements["option-" + question.id + "-" + option.id + "-label"];
          return {
            id: option.id,
            label: labelField ? labelField.value.trim() : option.label
          };
        })
      };
    });
  }

  function initEditPage(root) {
    var form = document.getElementById("experiment-form");
    if (!form) { return; }
    if (form.dataset.locked === "true") { return; }   // 已发布：只读，无保存

    var saveButton = root.querySelector('[data-testid="experiment-save"]');
    var status = root.querySelector('[data-testid="experiment-status"]');
    var errorBox = document.querySelector('[data-testid="field-errors"]');
    var experimentId = form.dataset.experimentId;
    var existing = window.__FO_EXPERIMENT__ || null;

    if (!experimentId || !existing || !saveButton) { return; }

    saveButton.addEventListener("click", function () {
      var landing = collectLanding(form);
      var questions = collectQuestions(form, existing.questions || []);

      if (!landing.title || !landing.summary || !landing.target_users ||
          !landing.value_proposition || !landing.disclaimer) {
        setStatus(status, "保存失败：服务文案的必填项不能为空。", "error");
        return;
      }
      if (landing.features.length > 6) {
        setStatus(status, "保存失败：能力说明最多 6 项。", "error");
        return;
      }

      saveButton.disabled = true;
      setStatus(status, "正在保存…", null);
      showFieldErrors(errorBox, null);

      request("PATCH", "/api/experiments/" + encodeURIComponent(experimentId),
              { landing: landing, questions: questions },
              { admin: true, timeout: ADMIN_TIMEOUT_MS })
        .then(function (data) {
          window.__FO_EXPERIMENT__ = data;   // 后续保存沿用服务端返回的结构
          setStatus(status, "已保存。", "ok");
        })
        .catch(function (error) {
          setStatus(status, "保存失败：" + messageFor(error), "error");
          showFieldErrors(errorBox, error);
        })
        .then(function () {
          saveButton.disabled = false;      // 无论成败都恢复可用（E2E 依赖此行为）
        });
    });
  }

  /* ------------------------------------------------------------------ *
   * 问卷页（survey.html）
   * ------------------------------------------------------------------ */

  function storageKey(token) { return "fo-lab-submission:" + token; }

  function readStored(key) {
    try { return window.localStorage.getItem(key); } catch (error) { return null; }
  }

  function writeStored(key, value) {
    try { window.localStorage.setItem(key, value); } catch (error) { /* 隐私模式下忽略 */ }
  }

  function tokenFromSubmitUrl(url) {
    var match = /\/api\/public\/e\/([^/]+)\/responses/.exec(url || "");
    return match ? decodeURIComponent(match[1]) : "";
  }

  function questionIdFromName(name) {
    return name.indexOf("answer-") === 0 ? name.slice("answer-".length) : name;
  }

  function collectAnswers(form) {
    var questions = (form.dataset.questions ? JSON.parse(form.dataset.questions) : []) || [];
    var answers = {};
    var missing = [];

    questions.forEach(function (question) {
      var name = "answer-" + question.id;
      var fields = form.querySelectorAll('[name="' + name + '"]');
      var value = null;

      if (question.type === "single") {
        for (var i = 0; i < fields.length; i += 1) {
          if (fields[i].checked) { value = fields[i].value; break; }
        }
      } else if (question.type === "multiple") {
        var picked = [];
        for (var j = 0; j < fields.length; j += 1) {
          if (fields[j].checked) { picked.push(fields[j].value); }
        }
        value = picked.length ? picked.sort() : null;
      } else {
        var text = fields.length ? fields[0].value.trim() : "";
        value = text.length ? text : null;
      }

      if (value === null) {
        if (question.required) { missing.push(question); }
        return;                      // 可选题未作答：从 answers 中省略
      }
      answers[question.id] = value;
    });

    return { answers: answers, missing: missing };
  }

  function showSubmissionSuccess(form, submittedAt) {
    var successBox = document.querySelector('[data-testid="submission-success"]');
    var timeSlot = document.querySelector('[data-testid="submission-time"]');
    if (timeSlot) { timeSlot.textContent = submittedAt || new Date().toLocaleString(); }
    if (successBox) { successBox.hidden = false; }
    if (form) {
      Array.prototype.forEach.call(form.elements, function (element) {
        if (element.type !== "hidden") { element.disabled = true; }
      });
    }
  }

  function initSurveyPage(root) {
    var form = document.getElementById("survey-form");
    if (!form) { return; }

    var status = root.querySelector('[data-testid="survey-status"]');
    var errorBox = document.querySelector('[data-testid="field-errors"]');
    var submitButton = root.querySelector('[data-testid="survey-submit"]');
    var consent = root.querySelector('[data-testid="survey-consent"]');
    var submitUrl = form.dataset.submitUrl || "";
    var isPreview = form.dataset.isPreview === "true";

    if (isPreview || !submitUrl) {
      // 草稿预览：后端已把控件 disabled，这里再兜一层，并给出可读说明
      if (submitButton) { submitButton.disabled = true; }
      setStatus(status, "这是草稿预览，不能提交。", null);
      return;
    }

    var token = tokenFromSubmitUrl(submitUrl);
    var key = storageKey(token);
    var submissionId = readStored(key) || uuid();
    writeStored(key, submissionId);

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (submitButton && submitButton.disabled) { return; }

      var collected = collectAnswers(form);
      if (collected.missing.length) {
        setStatus(status, "还有必答题未作答，请先完成后再提交。", "error");
        var first = document.querySelector(
          '[data-testid="question-' + collected.missing[0].id + '"]'
        );
        if (first && first.scrollIntoView) { first.scrollIntoView({ block: "center" }); }
        return;
      }
      if (!consent || !consent.checked) {
        setStatus(status, "请先勾选同意参与，再提交问卷。", "error");
        return;
      }

      var payload = {
        client_submission_id: submissionId,
        consent: true,
        answers: collected.answers
      };

      if (submitButton) { submitButton.disabled = true; }
      setStatus(status, "正在提交…", null);
      showFieldErrors(errorBox, null);

      request("POST", submitUrl, payload, { timeout: PUBLIC_TIMEOUT_MS })
        .then(function (data) {
          writeStored(key, submissionId);       // 固定本次提交编号，重发沿用同一编号
          setStatus(status, data && data.duplicate
            ? "这份回答此前已提交过，未重复记录。"
            : "提交成功，感谢参与。", "ok");
          showSubmissionSuccess(form, data && data.submitted_at);
        })
        .catch(function (error) {
          if (error && error.code === "IDEMPOTENCY_CONFLICT") {
            // 同一编号已用于不同内容：换新编号，用户重新提交即可
            submissionId = uuid();
            writeStored(key, submissionId);
            setStatus(status, "本次填写内容已变更，已准备重新提交，请再点一次提交。", "error");
          } else {
            setStatus(status, "提交失败：" + messageFor(error), "error");
            showFieldErrors(errorBox, error);
          }
          if (submitButton) { submitButton.disabled = false; }   // 保留已填答案，允许重试
        });
    });
  }

  /* ------------------------------------------------------------------ *
   * 启动
   * ------------------------------------------------------------------ */

  function boot() {
    var body = document.body;
    if (body.classList.contains("page-admin")) { initEditPage(document); }
    if (body.classList.contains("page-survey")) { initSurveyPage(document); }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
}());
