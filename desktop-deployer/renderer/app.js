'use strict';

(() => {
  const icons = {
    grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.87M15 3.13a4 4 0 0 1 0 7.75"/><circle cx="9" cy="7" r="4"/>',
    activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    cloud: '<path d="M19 10a7 7 0 1 0-13.6 3.3A4.5 4.5 0 0 0 7.5 22h11a6 6 0 0 0 .5-12Z" transform="translate(0 -3)"/>',
    github: '<path d="M9 19c-4.3 1.3-4.3-2.2-6-2.7M15 22v-3.9a3.4 3.4 0 0 0-.94-2.65c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 19 4.67 5.07 5.07 0 0 0 18.91 1S17.73.65 15 2.48a13.38 13.38 0 0 0-7 0C5.27.65 4.09 1 4.09 1A5.07 5.07 0 0 0 4 4.67a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.4 3.4 0 0 0 8 18.1V22" transform="translate(1 .5) scale(.94)"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18-3-3.2-3-14.8 0-18Z"/>',
    repository: '<path d="M4 5a2 2 0 0 1 2-2h13v18H6a2 2 0 0 1-2-2V5Zm0 12a2 2 0 0 1 2-2h13M8 3v7l2-1 2 1V3"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.01"/>',
    rocket: '<path d="m9 15-3 3m3-10-5 1-2 5 6 1m8-6 5 1-1 5-5 1M8 15c0-7 6-12 13-12 0 7-5 13-12 13L8 15Z"/><circle cx="16" cy="8" r="1"/><path d="M5 16c-2 0-3 3-3 5 2 0 5-1 5-3"/>',
    arrowRight: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    arrowUpRight: '<path d="M6 18 18 6M6 6h12v12"/>',
    chevronDown: '<path d="m6 9 6 6 6-6"/>',
    chevronUp: '<path d="m6 15 6-6 6 6"/>',
    sparkles: '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Zm7-2v4m-2-2h4M3 18v4m-2-2h4"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    alert: '<path d="M10.3 3.8 2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.8a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4m0 4h.01"/>',
    lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/>',
    layers: '<path d="m12 3 10 5-10 5L2 8l10-5Zm-9 10 9 5 9-5M3 18l9 5 9-5" transform="translate(0 -1)"/>',
    terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3m6 0h4"/>',
    refresh: '<path d="M20 11a8 8 0 0 0-14.9-4L2 10m0-6v6h6M4 13a8 8 0 0 0 14.9 4l3.1-3m0 6v-6h-6"/>',
    external: '<path d="M15 3h6v6m0-6L10 14M9 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-4"/>',
    pause: '<path d="M8 5v14M16 5v14" stroke-width="3"/>',
    play: '<path d="m8 4 12 8-12 8V4Z"/>',
    copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
    loader: '<path d="M21 12a9 9 0 1 1-9-9"/>',
    browser: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M7 6.5h.01m3 0h.01"/>',
    compass: '<circle cx="12" cy="12" r="9"/><path d="m16 8-2 6-6 2 2-6 6-2Z"/>',
  };

  const defaults = {
    version: '', platform: '', dataDirectory: '', busy: false, stage: 'idle', message: '', error: '',
    github: null, browsers: [], selectedBrowser: '', settings: { repoName: 'glados-quick-deploy', exchangePlan: 'plan500', time: '09:30' },
    authCode: null, accounts: [], events: [], currentRun: null,
  };

  let state = { ...defaults, settings: { ...defaults.settings } };
  let draft = { ...defaults.settings, selectedBrowser: '' };
  let initializedDraft = false;
  let draftDirty = false;
  let saveTimer = null;
  let toastTimer = null;
  let awaitingAction = false;
  let lastActiveStep = 0;
  let eventsSignature = '';
  let accountsSignature = '';
  let browserSignature = '';
  let localError = '';
  let emptyAccountsTemplate = null;
  const pendingAccounts = new Set();
  const $ = (id) => document.getElementById(id);

  function iconMarkup(name) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (icons[name] || icons.info) + '</svg>';
  }

  function setIcon(element, name) {
    if (!element || element.dataset.renderedIcon === name) return;
    element.innerHTML = iconMarkup(name);
    element.dataset.renderedIcon = name;
  }

  function makeIcon(name, extraClass = '') {
    const element = document.createElement('span');
    element.className = 'icon' + (extraClass ? ' ' + extraClass : '');
    element.setAttribute('aria-hidden', 'true');
    setIcon(element, name);
    return element;
  }

  function safeText(value) {
    return String(value ?? '')
      .replace(/\b(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+\b/g, '[已隐藏令牌]')
      .replace(/\bBearer\s+[A-Za-z0-9._~+\/-]+=*/gi, 'Bearer [已隐藏]')
      .replace(/\b(?:gld|koa):sess(?:\.sig)?\s*=\s*[^;\s]+/gi, '[已隐藏会话]');
  }

  function text(element, value) {
    if (element) element.textContent = safeText(value);
  }

  function makeText(tag, className, value) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    text(element, value);
    return element;
  }

  function toast(message) {
    window.clearTimeout(toastTimer);
    text($('toast'), message);
    $('toast').hidden = false;
    toastTimer = window.setTimeout(() => { $('toast').hidden = true; }, 3600);
  }

  function isBusy() { return Boolean(state.busy || awaitingAction); }

  function stageInfo() {
    const stage = String(state.stage || 'idle').toLowerCase();
    const configurations = {
      idle: { step: state.github ? 1 : 0, done: state.github ? 1 : 0, title: '准备好开始了', badge: '准备就绪', icon: 'sparkles', kind: 'idle', message: '确认部署设置后点击「一键部署」。只需在提示时完成登录。' },
      initializing: { step: 0, done: 0, title: '正在准备运行环境', badge: '准备中', icon: 'loader', kind: 'working', message: '初始化完成后即可开始部署。' },
      github_auth: { step: 0, done: 0, title: '请完成 GitHub 授权', badge: '等待授权', icon: 'github', kind: 'working', message: '在打开的 GitHub 页面完成授权，程序会自动继续。' },
      browser_login: { step: 1, done: 1, title: '请登录 GLaDOS', badge: '等待登录', icon: 'browser', kind: 'working', message: '在所选浏览器中完成登录，成功后自动接续部署。' },
      deploying: { step: 2, done: 2, title: '正在自动部署', badge: '部署中', icon: 'cloud', kind: 'working', message: '同步项目、配置运行参数并启动首次验证。' },
      verifying: { step: 3, done: 3, title: '正在验证首次运行', badge: '验证中', icon: 'activity', kind: 'working', message: '等待 GitHub Actions 返回实际运行结果。' },
      complete: { step: 4, done: 4, title: '部署流程已完成', badge: '部署完成', icon: 'check', kind: 'complete', message: '实际签到结果请查看下方账号状态。' },
      error: { step: lastActiveStep, done: lastActiveStep, title: '需要处理一个问题', badge: '需要处理', icon: 'alert', kind: 'error', message: '请查看具体提示，处理后重新部署或刷新运行结果。' },
      cancelling: { step: lastActiveStep, done: lastActiveStep, title: '正在取消任务', badge: '取消中', icon: 'loader', kind: 'working', message: '正在安全结束当前步骤。' },
      cancelled: { step: lastActiveStep, done: lastActiveStep, title: '本次任务已取消', badge: '已取消', icon: 'pause', kind: 'idle', message: '已完成的配置会保留，可随时重新开始。' },
    };
    const result = configurations[stage] || (isBusy()
      ? { step: lastActiveStep, done: lastActiveStep, title: '任务正在进行', badge: '进行中', icon: 'loader', kind: 'working', message: '程序正在处理当前步骤。' }
      : configurations.idle);
    if (['github_auth', 'browser_login', 'deploying', 'verifying'].includes(stage)) lastActiveStep = result.step;
    if (stage === 'complete' && state.currentRun) {
      const runResult = resultInfo(state.currentRun);
      if (runResult.kind === 'pending') return { step: 3, done: 3, title: '部署完成，等待验证', badge: '等待验证', icon: 'clock', kind: 'working', message: '任务已配置，首次运行仍在排队或执行。可在账号卡片刷新结果。' };
      if (runResult.kind === 'error') return { step: 3, done: 3, title: '部署完成，验证未通过', badge: '需要处理', icon: 'alert', kind: 'error', message: '请查看本次运行记录，处理具体问题后重新验证。' };
    }
    if (stage === 'idle' && isBusy()) return configurations.initializing;
    return result;
  }

  function renderConnection() {
    const connected = Boolean(state.github && state.github.login);
    text($('github-connect-label'), connected ? state.github.login : '连接 GitHub');
    $('github-connection-dot').classList.toggle('is-connected', connected);
    $('github-connect').disabled = isBusy();
    $('github-connect').title = connected ? '已连接 ' + safeText(state.github.login) + '；点击重新授权' : '连接用于部署的 GitHub 账号';
    $('github-connect').setAttribute('aria-label', connected ? 'GitHub 已连接：' + safeText(state.github.login) + '，点击重新授权' : '连接 GitHub');
    text($('app-version'), state.version ? 'v' + String(state.version).replace(/^v/, '') : '桌面版');
    const platformNames = { darwin: 'macOS', win32: 'Windows', linux: 'Linux' };
    text($('app-platform'), platformNames[state.platform] || state.platform || '正在连接');
    text($('data-directory'), state.dataDirectory || '应用启动后自动配置');
  }

  function renderBrowsers() {
    const browsers = Array.isArray(state.browsers) ? state.browsers : [];
    const signature = JSON.stringify(browsers);
    if (signature !== browserSignature) {
      browserSignature = signature;
      const select = $('browser-select');
      select.replaceChildren();
      if (!browsers.length) {
        const option = makeText('option', '', '正在识别浏览器…');
        option.value = '';
        select.append(option);
      } else {
        for (const browser of browsers) {
          const available = browser.available !== false;
          const unavailableLabel = /safari/i.test(String(browser.id || '') + ' ' + String(browser.family || '')) ? ' · 仅用于 GitHub 授权' : ' · 未安装';
          const option = makeText('option', '', browser.name + (available ? '' : unavailableLabel));
          option.value = String(browser.id);
          option.disabled = !available;
          select.append(option);
        }
      }
    }
    const available = browsers.filter((browser) => browser.available !== false);
    if (!available.some((browser) => browser.id === draft.selectedBrowser) && available.length) {
      const preferred = available.find((browser) => browser.id === state.selectedBrowser)
        || available.find((browser) => /brave/i.test(browser.id)) || available[0];
      draft.selectedBrowser = preferred.id;
    }
    $('browser-select').value = draft.selectedBrowser;
    $('browser-select').disabled = isBusy() || !available.length;
    const selected = browsers.find((browser) => browser.id === draft.selectedBrowser);
    const family = selected ? String(selected.family || '').toLowerCase() : '';
    const browserId = selected ? String(selected.id || '').toLowerCase() : '';
    let description = selected && selected.details ? safeText(selected.details) : '程序会打开专用登录窗口，完成 GLaDOS 登录后自动继续。';
    let familyLabel = '浏览器';
    let symbolName = 'globe';
    let symbolClass = '';
    if (/embedded|builtin|built-in/.test(browserId + ' ' + family)) {
      familyLabel = '无需安装'; symbolName = 'browser';
      if (!(selected && selected.details)) description = '使用应用内置的独立登录窗口，无需额外安装浏览器。';
    } else if (/firefox/.test(browserId + ' ' + family)) {
      familyLabel = '兼容通道'; symbolClass = 'is-firefox';
      if (!(selected && selected.details)) description = '通过 Firefox 专用兼容通道登录，完成后自动接续。';
    } else if (/safari/.test(browserId + ' ' + family)) {
      familyLabel = '备用通道'; symbolName = 'compass'; symbolClass = 'is-safari';
      if (!(selected && selected.details)) description = 'Safari 使用备用登录通道；请按照打开窗口中的说明完成登录。';
    } else if (selected) {
      familyLabel = /chromium|chrome|edge|brave|opera|vivaldi/.test(browserId + ' ' + family) ? 'Chromium' : '已检测';
      symbolClass = /brave/.test(browserId) ? 'is-brave' : /edge/.test(browserId) ? 'is-edge' : /chrome/.test(browserId) ? 'is-chrome' : '';
    }
    $('browser-symbol').className = 'browser-symbol' + (symbolClass ? ' ' + symbolClass : '');
    setIcon($('browser-symbol').querySelector('.icon'), symbolName);
    text($('browser-family'), familyLabel);
    text($('browser-description'), description);
    text($('browser-discovery'), available.length ? '已识别 ' + available.length + ' 种可用方式' : '正在识别可用浏览器');
  }

  function renderSettings() {
    for (const [id, key] of [['repository-input', 'repoName'], ['exchange-select', 'exchangePlan'], ['schedule-input', 'time']]) {
      const element = $(id);
      if (document.activeElement !== element && element.value !== draft[key]) element.value = draft[key];
      element.disabled = isBusy();
    }
    const hasBrowser = state.browsers.some((browser) => browser.id === draft.selectedBrowser && browser.available !== false);
    $('deploy-button').disabled = isBusy() || !hasBrowser || !window.quickDeploy;
    text($('deploy-button-label'), isBusy() ? '任务正在进行…' : state.accounts.length ? '部署另一个账号' : '一键部署');
    setIcon($('deploy-button-icon'), isBusy() ? 'loader' : 'rocket');
    $('deploy-button-icon').classList.toggle('is-spinning', isBusy());
    $('cancel-button').hidden = !isBusy();
    $('cancel-button').disabled = state.stage === 'cancelling';
    text($('cancel-button'), state.stage === 'cancelling' ? '正在取消…' : '取消任务');
    const hints = {
      github_auth: '请在浏览器中完成 GitHub 授权，程序会自动继续。',
      browser_login: '请在打开的登录窗口完成登录，暂时无需其他操作。',
      deploying: '正在自动配置。你可以留在本页查看实时进度。',
      verifying: 'GitHub 任务正在排队或运行，结果会自动更新。',
      cancelling: '正在结束当前步骤，请稍候。',
    };
    text($('deploy-hint'), hints[state.stage] || '需要时会打开登录页，登录完成后自动接续。');
  }

  function renderProgress() {
    const info = stageInfo();
    $('overview-badge').className = 'overview-badge is-' + info.kind;
    text($('overview-text'), info.badge);
    text($('progress-count'), info.done + ' / 4');
    $('progress-visual').className = 'progress-visual is-' + info.kind;
    setIcon($('progress-center-icon'), info.icon);
    $('progress-center-icon').classList.toggle('is-spinning', info.icon === 'loader');
    text($('progress-title'), info.title);
    text($('progress-message'), state.message || info.message);
    const stage = String(state.stage || 'idle');
    const active = (!['idle', 'cancelled', 'complete'].includes(stage) && (isBusy() || stage === 'error')) || (stage === 'complete' && info.done < 4);
    for (const item of $('progress-steps').children) {
      const index = Number(item.dataset.step);
      const done = index < info.done;
      const current = index === info.step && active;
      const failed = current && info.kind === 'error';
      item.classList.toggle('is-done', done);
      item.classList.toggle('is-current', current && !failed);
      item.classList.toggle('is-failed', failed);
      const indicator = item.querySelector('.step-indicator');
      if (done) { indicator.replaceChildren(makeIcon('check')); }
      else { text(indicator, index + 1); }
      const status = failed ? '需处理' : done ? '已完成' : current ? stage === 'cancelling' ? '取消中' : '进行中' : '';
      text(item.querySelector('.step-state'), status);
      item.setAttribute('aria-label', item.querySelector('strong').textContent + '：' + (status || '未开始'));
      if (current) item.setAttribute('aria-current', 'step');
      else item.removeAttribute('aria-current');
    }
    const auth = state.authCode && state.authCode.code && stage === 'github_auth';
    $('github-auth').hidden = !auth;
    text($('auth-code'), auth ? state.authCode.code : '');
    const run = state.currentRun;
    $('current-run').hidden = !run;
    if (run) {
      const status = resultInfo(run);
      text($('current-run-title'), status.label);
      text($('current-run-detail'), (run.repository || '') + (run.runId ? ' · #' + run.runId : ''));
    }
    const error = localError || state.error;
    $('error-banner').hidden = !error;
    text($('error-message'), error && typeof error === 'object' ? error.message || '当前步骤未完成，请查看运行记录后重试。' : error);
  }

  function resultInfo(account) {
    const conclusion = String(account.conclusion || account.outcome || '').toLowerCase();
    const status = String(account.status || '').toLowerCase();
    const points = Number(account.pointsAdded);
    const added = Number.isFinite(points) && points > 0;
    const creditSuffix = added ? ' · 已加分 +' + points : '';
    if (account.paused) return { kind: 'neutral', label: '仓库已暂停', detail: '此仓库全部账号已暂停按计划运行' };
    if (['authentication_rejected', 'authentication_required'].includes(conclusion)) return { kind: 'error', label: '需要重新登录' + creditSuffix, detail: '登录授权被拒绝，请重新登录后更新' };
    if (['failure', 'failed', 'error', 'timed_out', 'action_required', 'startup_failure', 'stale'].includes(conclusion)) return { kind: 'error', label: (conclusion === 'timed_out' ? '运行超时' : '运行异常') + creditSuffix, detail: added ? '已取得积分，但本次任务仍有异常；请查看运行记录' : '查看记录，处理后重新验证' };
    if (conclusion === 'cancelled' || conclusion === 'canceled') return { kind: 'neutral', label: '运行已取消' + creditSuffix, detail: added ? '取消前已取得积分，后续步骤未全部完成' : '可刷新查看最新记录' };
    if (status === 'in_progress' || conclusion === 'in_progress' || conclusion === 'running') return { kind: 'pending', label: '正在验证', detail: '等待本次运行返回结果' };
    if (['queued', 'pending', 'requested', 'waiting'].includes(status) || ['queued', 'pending', 'requested', 'waiting'].includes(conclusion)) return { kind: 'pending', label: '验证排队', detail: '本次验证尚未完成，请等待实际运行结果' };
    if (status === 'not_started' || conclusion === 'not_started') return { kind: 'neutral', label: '尚未验证', detail: '当前还没有可核验的运行记录' };
    if (conclusion === 'unverified') return { kind: 'neutral', label: '结果待核实' + creditSuffix, detail: '尚未取得完整签到结果，请刷新或查看运行记录' };
    if (added) return { kind: 'success', label: '已加分 +' + points, detail: '本次签到已取得积分' };
    if (['already_checked_in', 'already-checked-in', 'checkin_already_done', 'already_checked', 'already'].includes(conclusion)) return { kind: 'success', label: '今日已签到', detail: '本次验证确认今日已签到' };
    if (['checked', 'checked_in', 'checkin_success', 'points_added', 'accepted'].includes(conclusion)) return { kind: 'success', label: '签到成功', detail: '已确认签到结果' };
    if (conclusion === 'success') return { kind: 'success', label: '工作流成功', detail: '签到结果以具体运行记录为准' };
    if (conclusion === 'skipped' || conclusion === 'neutral') return { kind: 'neutral', label: '本次未执行', detail: '查看工作流记录了解原因' };
    return { kind: 'pending', label: '验证排队', detail: '等待 GitHub Actions 运行' };
  }

  function formatDate(value, withDate = false) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return safeText(value);
    return new Intl.DateTimeFormat('zh-CN', withDate
      ? { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }
      : { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(date);
  }

  function accountButton(account, action, icon, label, visibleLabel = false, payload = {}) {
    const button = document.createElement('button');
    button.className = 'account-action' + (action === 'pause' && !account.paused ? ' is-danger' : '');
    button.type = 'button';
    button.title = label;
    button.setAttribute('aria-label', label + '：' + safeText(account.email || account.accountKey));
    button.append(makeIcon(icon));
    if (visibleLabel) button.append(makeText('span', 'action-label', label));
    button.disabled = pendingAccounts.has(account.accountKey) || ((action === 'pause' || action === 'refreshRun') && isBusy());
    if (action === 'openRun' && !account.runId && !account.runUrl) button.disabled = true;
    button.addEventListener('click', () => accountAction(account.accountKey, action, payload));
    return button;
  }

  function renderAccounts(force = false) {
    text($('accounts-count'), state.accounts.length);
    text($('nav-account-count'), state.accounts.length);
    const signature = JSON.stringify([state.accounts, Array.from(pendingAccounts), isBusy()]);
    if (!force && signature === accountsSignature) return;
    accountsSignature = signature;
    const list = $('accounts-list');
    if (!emptyAccountsTemplate && list.querySelector('.empty-accounts')) emptyAccountsTemplate = list.firstElementChild.cloneNode(true);
    if (!state.accounts.length) {
      if (!list.querySelector('.empty-accounts') && emptyAccountsTemplate) list.replaceChildren(emptyAccountsTemplate.cloneNode(true));
      return;
    }
    list.replaceChildren();
    for (const account of state.accounts) {
      const card = document.createElement('article');
      card.className = 'account-card';
      const identity = document.createElement('div');
      identity.className = 'account-identity';
      const email = account.email || account.accountKey || 'GLaDOS 账号';
      identity.append(makeText('div', 'account-avatar', String(email).charAt(0).toUpperCase()));
      const identityCopy = document.createElement('div');
      const emailLabel = makeText('h3', 'account-email', email);
      emailLabel.title = safeText(email);
      identityCopy.append(emailLabel);
      const repository = makeText('p', 'account-repository', account.repository || '正在配置仓库');
      repository.title = safeText(account.repository || '');
      identityCopy.append(repository);
      identity.append(identityCopy);
      card.append(identity);

      const status = resultInfo(account);
      const statusBlock = document.createElement('div');
      statusBlock.className = 'account-state';
      const badge = document.createElement('span');
      badge.className = 'account-status is-' + status.kind;
      const dot = document.createElement('span');
      dot.className = 'status-dot';
      dot.setAttribute('aria-hidden', 'true');
      badge.append(dot, makeText('span', '', status.label));
      statusBlock.append(badge);
      const detail = safeText(account.paused ? status.detail : account.message || status.detail);
      const detailElement = makeText('span', 'account-detail', detail);
      if (account.updatedAt) detailElement.title = '更新于 ' + formatDate(account.updatedAt, true);
      statusBlock.append(detailElement);
      card.append(statusBlock);

      const actions = document.createElement('div');
      actions.className = 'account-actions';
      actions.append(accountButton(account, 'refreshRun', 'refresh', '刷新结果'));
      actions.append(accountButton(account, 'openRun', 'external', '运行记录', true));
      actions.append(accountButton(account, 'openRepository', 'repository', '打开仓库'));
      actions.append(accountButton(account, 'pause', account.paused ? 'play' : 'pause', account.paused ? '恢复此仓库全部账号' : '暂停此仓库全部账号', false, { paused: !account.paused }));
      card.append(actions);
      list.append(card);
    }
  }

  function renderEvents() {
    const events = state.events.slice(-50).reverse();
    const signature = JSON.stringify(events);
    text($('activity-count'), state.events.length ? '最近 ' + Math.min(state.events.length, 50) + ' 条' : '暂无记录');
    if (signature === eventsSignature) return;
    eventsSignature = signature;
    const list = $('activity-list');
    list.replaceChildren();
    if (!events.length) { list.append(makeText('p', 'empty-activity', '操作与进度会实时记录在这里。')); return; }
    for (const event of events) {
      const row = document.createElement('div');
      const level = ['error', 'warning', 'success', 'warn'].includes(event.level) ? event.level === 'warn' ? 'warning' : event.level : 'info';
      row.className = 'event-row is-' + level;
      const time = makeText('time', 'event-time', formatDate(event.time));
      const parsedDate = new Date(event.time);
      if (!Number.isNaN(parsedDate.getTime())) time.dateTime = parsedDate.toISOString();
      row.append(time);
      const dot = document.createElement('span');
      dot.className = 'event-dot'; dot.setAttribute('aria-hidden', 'true');
      row.append(dot, makeText('span', 'event-message', event.message || ''));
      list.append(row);
    }
  }

  function render() {
    renderConnection();
    renderBrowsers();
    renderSettings();
    renderProgress();
    renderAccounts();
    renderEvents();
  }

  function receiveState(next) {
    if (!next || typeof next !== 'object') return;
    state = {
      ...defaults, ...next,
      settings: { ...defaults.settings, ...(next.settings || {}) },
      browsers: Array.isArray(next.browsers) ? next.browsers : [],
      accounts: Array.isArray(next.accounts) ? next.accounts : [],
      events: Array.isArray(next.events) ? next.events : [],
    };
    if (!initializedDraft || !draftDirty) {
      draft = { ...state.settings, selectedBrowser: state.selectedBrowser || draft.selectedBrowser };
      initializedDraft = true;
    }
    render();
  }

  function gatherSettings() {
    return {
      selectedBrowser: $('browser-select').value,
      repoName: $('repository-input').value.trim(),
      exchangePlan: $('exchange-select').value,
      time: $('schedule-input').value || '09:30',
    };
  }

  async function callAction(name, payload = {}) {
    if (!window.quickDeploy || typeof window.quickDeploy.action !== 'function') throw new Error('桌面服务尚未连接，请关闭窗口后重新打开应用。');
    const result = await window.quickDeploy.action(name, payload);
    if (result && typeof result === 'object') receiveState(result);
    return result;
  }

  function captureDraft() {
    draft = gatherSettings();
    draftDirty = true;
    localError = '';
  }

  async function saveSettings() {
    if (!draftDirty || isBusy() || !window.quickDeploy) return;
    if (!$('repository-input').checkValidity() || !$('schedule-input').checkValidity()) return;
    const snapshot = { ...draft };
    try {
      const result = await window.quickDeploy.action('saveSettings', snapshot);
      if (JSON.stringify(snapshot) === JSON.stringify(draft)) draftDirty = false;
      if (result && typeof result === 'object') receiveState(result);
    } catch (error) {
      localError = safeText(error.message || '设置未能保存。请重试。');
      renderProgress();
    }
  }

  async function startDeploy(event) {
    if (event) event.preventDefault();
    if (isBusy()) return;
    if (!$('deploy-form').reportValidity()) return;
    captureDraft();
    if (!draft.selectedBrowser) { toast('请选择一种可用的登录方式。'); return; }
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(draft.repoName)) { toast('仓库名称须为 1–80 个字符，并以英文字母或数字开头。'); $('repository-input').focus(); return; }
    window.clearTimeout(saveTimer);
    localError = '';
    awaitingAction = true;
    lastActiveStep = state.github ? 1 : 0;
    render();
    try {
      await callAction('startDeploy', { browserId: draft.selectedBrowser, repoName: draft.repoName, exchangePlan: draft.exchangePlan, time: draft.time });
      draftDirty = false;
    } catch (error) {
      localError = safeText(error.message || '任务未能启动。请查看提示后重试。');
    } finally {
      awaitingAction = false;
      render();
    }
  }

  async function connectGithub() {
    if (isBusy()) return;
    localError = '';
    awaitingAction = true;
    render();
    try { await callAction('connectGithub'); }
    catch (error) { localError = safeText(error.message || 'GitHub 连接未完成，请重试。'); }
    finally { awaitingAction = false; render(); }
  }

  async function cancelTask() {
    $('cancel-button').disabled = true;
    text($('cancel-button'), '正在取消…');
    try { await callAction('cancel'); }
    catch (error) { localError = safeText(error.message || '取消请求未完成，请稍后再试。'); }
    finally { render(); }
  }

  async function accountAction(accountKey, name, payload) {
    if (pendingAccounts.has(accountKey)) return;
    pendingAccounts.add(accountKey);
    renderAccounts(true);
    try { await callAction(name, { accountKey, ...payload }); }
    catch (error) { toast(error.message || '操作未完成，请稍后重试。'); }
    finally { pendingAccounts.delete(accountKey); renderAccounts(true); }
  }

  function bindEvents() {
    $('deploy-form').addEventListener('submit', startDeploy);
    $('github-connect').addEventListener('click', connectGithub);
    $('cancel-button').addEventListener('click', cancelTask);
    $('copy-auth-code').addEventListener('click', async () => {
      try { await callAction('copyAuthCode'); toast('授权码已复制。'); }
      catch (error) { toast(error.message || '复制未完成，请手动选择授权码。'); }
    });
    for (const id of ['browser-select', 'repository-input', 'exchange-select', 'schedule-input']) {
      $(id).addEventListener('change', () => { captureDraft(); window.clearTimeout(saveTimer); renderBrowsers(); renderSettings(); saveTimer = window.setTimeout(saveSettings, 200); });
    }
    $('repository-input').addEventListener('input', () => { captureDraft(); window.clearTimeout(saveTimer); saveTimer = window.setTimeout(saveSettings, 500); });
    $('toggle-activity').addEventListener('click', () => {
      const collapsed = !$('activity-list').hidden;
      $('activity-list').hidden = collapsed;
      $('toggle-activity').setAttribute('aria-expanded', String(!collapsed));
      $('toggle-activity').replaceChildren(document.createTextNode(collapsed ? '展开' : '收起'), makeIcon(collapsed ? 'chevronDown' : 'chevronUp'));
    });
    document.querySelectorAll('[data-scroll]').forEach((button) => {
      button.addEventListener('click', () => {
        const target = $(button.dataset.scroll);
        if (target) target.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
        document.querySelectorAll('.nav-item').forEach((item) => {
          item.classList.toggle('is-active', item === button);
          if (item === button) item.setAttribute('aria-current', 'page');
          else item.removeAttribute('aria-current');
        });
      });
    });
    document.addEventListener('keydown', (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && !isBusy()) { event.preventDefault(); startDeploy(); }
    });
  }

  async function initialize() {
    document.querySelectorAll('[data-icon]').forEach((element) => setIcon(element, element.dataset.icon));
    bindEvents();
    render();
    if (!window.quickDeploy || typeof window.quickDeploy.getState !== 'function') {
      localError = '桌面服务尚未连接。请使用已安装的 Quick Deploy 应用打开此界面。';
      render();
      return;
    }
    try {
      if (typeof window.quickDeploy.onState === 'function') {
        const unsubscribe = window.quickDeploy.onState(receiveState);
        if (typeof unsubscribe === 'function') window.addEventListener('beforeunload', unsubscribe, { once: true });
      }
      receiveState(await window.quickDeploy.getState());
    } catch (error) {
      localError = safeText(error.message || '读取应用状态失败，请重新打开应用。');
      render();
    }
  }

  initialize();
})();
