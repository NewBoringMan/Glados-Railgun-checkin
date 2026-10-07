'use strict';

const { EventEmitter } = require('node:events');
const { DEFAULT_SETTINGS, cleanSettings, cleanAccount } = require('./state.cjs');

const PENDING = new Set(['queued', 'pending', 'requested', 'waiting', 'in_progress']);
const FAILED = new Set(['failure', 'cancelled', 'timed_out', 'action_required', 'startup_failure', 'stale']);

function safeMessage(value, secrets = []) {
  let text = String(value || '').slice(0, 4000);
  for (const secret of secrets) if (secret && secret.length >= 8) text = text.split(secret).join('[已隐藏]');
  return text
    .replace(/(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+/g, '[已隐藏授权]')
    .replace(/((?:gld|koa):sess(?:\.sig)?\s*=)[^;\s]+/gi, '$1[已隐藏]')
    .replace(/(authorization\s*[:=]\s*)(?:bearer|token)\s+\S+/gi, '$1[已隐藏]')
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '')
    .slice(0, 800);
}

function aborted(error, signal) { return signal?.aborted || error?.name === 'AbortError' || ['ABORT_ERR', 'LOGIN_CANCELLED', 'CANCELLED'].includes(error?.code); }

function pauseFor(ms, signal) {
  if (signal.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
  return new Promise((resolve, reject) => {
    const finish = () => { signal.removeEventListener('abort', cancel); resolve(); };
    const timer = setTimeout(finish, ms);
    const cancel = () => { clearTimeout(timer); signal.removeEventListener('abort', cancel); reject(new DOMException('Aborted', 'AbortError')); };
    signal.addEventListener('abort', cancel, { once: true });
  });
}

function evidenceFor(result) {
  return result.result?.accounts?.find(account => account.accountKey === result.accountKey) || result.result || {};
}

class Controller extends EventEmitter {
  constructor(options) {
    super();
    this.options = options;
    this.github = options.github;
    this.captureLogin = options.captureLogin;
    this.secrets = [];
    this.abort = null;
    this.pendingAccount = null;
    this.closing = false;
    this.activity = 0;
    this.backgroundAbort = null;
    this.backgroundTask = null;
    this.operationDone = Promise.resolve();
    this.initializeAbort = null;
    this.initializeTask = null;
    const restored = options.restored || {};
    this.state = {
      version: options.version || '1.0.0', platform: options.platform || process.platform,
      dataDirectory: options.dataDirectory || '', busy: false, stage: 'idle',
      message: '完成登录后，软件会自动部署到你的 GitHub。', error: '', github: null,
      browsers: [], selectedBrowser: restored.selectedBrowser || '',
      settings: cleanSettings(restored.settings || DEFAULT_SETTINGS), authCode: null,
      accounts: (restored.accounts || []).map(cleanAccount).filter(Boolean), events: [], currentRun: null,
    };
  }

  snapshot() { return JSON.parse(JSON.stringify(this.state)); }
  changed() { this.emit('state', this.snapshot()); }
  persist() { this.options.save?.(this.state); }
  note(message, level = 'info') {
    const safe = safeMessage(message, this.secrets);
    if (!safe) return;
    this.state.events.push({ time: new Date().toISOString(), level, message: safe });
    this.state.events = this.state.events.slice(-150);
    this.state.message = safe;
    this.changed();
  }

  onGitHubEvent(event = {}) {
    if (this.closing) return;
    if (event.type === 'auth-code' && /^[A-Z0-9-]{4,20}$/.test(event.code || '')) {
      this.state.authCode = { code: event.code, url: 'https://github.com/login/device' };
      this.state.stage = 'github_auth';
    }
    if (event.type === 'run' && event.repository && event.runId) {
      this.state.stage = 'verifying';
      this.state.currentRun = {
        repository: event.repository, runId: event.runId,
        runUrl: event.runUrl || `https://github.com/${event.repository}/actions/runs/${event.runId}`,
        status: event.status || 'queued', conclusion: event.conclusion || null,
      };
      if (this.pendingAccount && event.credentialUpdated !== false) this.upsert({ ...this.pendingAccount, ...this.state.currentRun, conclusion: event.conclusion || event.status || 'queued' });
    }
    this.note(event.message || (event.type === 'run' ? 'GitHub 已接收验证任务，正在等待实际结果。' : ''), event.level || 'info');
    this.changed();
  }

  initialize() {
    if (this.initializeTask) return this.initializeTask;
    this.initializeAbort = new AbortController();
    const signal = this.initializeAbort.signal;
    this.initializeTask = (async () => {
      this.state.browsers = await this.options.discoverBrowsers();
      if (!this.state.browsers.some(b => b.id === this.state.selectedBrowser && b.available)) {
        this.state.selectedBrowser = this.state.browsers.find(b => b.available && b.id !== 'embedded')?.id || 'embedded';
      }
      if (signal.aborted) return;
      try { this.state.github = await this.github.whoami({ signal }); } catch { this.state.github = null; }
      if (!this.closing) this.changed();
    })().finally(() => { this.initializeAbort = null; this.initializeTask = null; });
    return this.initializeTask;
  }

  upsert(account) {
    const record = cleanAccount({ ...account, updatedAt: new Date().toISOString() });
    if (!record) throw new Error('账号记录不完整，未写入本地配置。');
    const index = this.state.accounts.findIndex(item => item.accountKey === record.accountKey);
    if (index >= 0) this.state.accounts[index] = { ...this.state.accounts[index], ...record };
    else this.state.accounts.push(record);
    this.persist();
    this.changed();
  }

  async exclusive(work) {
    if (this.closing) throw new Error('软件正在退出，请重新打开后继续。');
    if (this.state.busy) throw new Error('当前任务仍在进行，请等待完成或点击取消。');
    this.activity++;
    this.backgroundAbort?.abort();
    const priorBackground = this.backgroundTask;
    let finishOperation;
    this.operationDone = new Promise(resolve => { finishOperation = resolve; });
    this.abort = new AbortController();
    const signal = this.abort.signal;
    this.state.busy = true; this.state.error = ''; this.state.authCode = null;
    this.changed();
    try {
      if (priorBackground) await priorBackground.catch(() => {});
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
      await work(signal);
    }
    catch (error) {
      if (aborted(error, signal)) {
        this.state.stage = 'cancelled';
        this.note(this.state.currentRun ? '已停止本机等待；已提交的 GitHub 任务仍可在账号卡片中查看。' : '已取消。登录窗口和本次临时会话会自动关闭。', 'warning');
      } else {
        this.state.stage = 'error';
        this.state.error = safeMessage(error?.message || '操作未完成，请检查网络后重试。', this.secrets);
        this.note(this.state.error, 'error');
      }
    } finally {
      this.secrets.length = 0; this.abort = null; this.pendingAccount = null;
      this.state.busy = false; this.state.authCode = null;
      this.changed();
      finishOperation();
    }
    return this.snapshot();
  }

  async ensureGitHub(signal, forceLogin = false) {
    let identity;
    try { if (!forceLogin) identity = await this.github.whoami({ signal }); }
    catch (error) {
      if (error.code !== 'AUTH_REQUIRED' && error.status !== 401) throw error;
    }
    if (!identity) {
      this.state.stage = 'github_auth';
      this.note('请在浏览器中完成 GitHub 登录和官方授权。');
      identity = await this.github.login({ signal });
    }
    if (!identity?.login || !/^[A-Za-z0-9-]+$/.test(identity.login)) throw new Error('没有取得有效的 GitHub 账号身份。');
    this.state.github = identity; this.state.authCode = null; this.changed();
    return identity;
  }

  async startDeploy(payload = {}) {
    return this.exclusive(async signal => {
      const requested = { ...this.state.settings, ...payload };
      const settings = cleanSettings(requested);
      if (requested.repoName !== settings.repoName || requested.time !== settings.time || requested.exchangePlan !== settings.exchangePlan) throw new Error('仓库名称、签到时间或兑换计划格式不正确。');
      const browserId = payload.browserId || this.state.selectedBrowser;
      if (!this.state.browsers.some(b => b.id === browserId && b.available)) throw new Error('所选浏览器不可用，请使用内置登录窗口。');
      this.state.settings = settings; this.state.selectedBrowser = browserId; this.state.currentRun = null;
      this.persist();
      await this.ensureGitHub(signal);
      this.state.stage = 'browser_login';
      this.note('请在专用窗口登录 GLaDOS。登录成功后会自动继续，无需复制 Cookie。');
      const credential = await this.captureLogin({ browserId, signal, profileRoot: this.options.profileRoot, parentWindow: this.options.parentWindow?.(), onProgress: event => this.note(event.message || event) });
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
      this.secrets.push(credential.cookie);
      for (const segment of String(credential.cookie).split(';')) {
        const i = segment.indexOf('='); if (i >= 0) this.secrets.push(segment.slice(i + 1).trim());
      }
      this.pendingAccount = { accountKey: credential.accountKey, email: credential.email || '', browser: credential.browser || browserId };
      this.state.stage = 'deploying';
      this.note('登录身份已核验，正在配置专用仓库和该账号的加密登录信息。');
      let renewedAuthorization = false;
      const deploy = async () => {
        try { return await this.github.deploy({ credential, ...settings, signal }); }
        catch (error) {
          if (renewedAuthorization || !['WORKFLOW_AUTH_REQUIRED', 'AUTH_REQUIRED'].includes(error.code)) throw error;
          renewedAuthorization = true;
          this.note('GitHub 需要补充授权。请完成官方登录，随后会自动接续本次部署。');
          await this.ensureGitHub(signal, true);
          this.state.stage = 'deploying';
          return this.github.deploy({ credential, ...settings, signal });
        }
      };
      let result = await deploy();
      const waitingSince = Date.now();
      while (result.deploymentPending) {
        if (Date.now() - waitingSince > 15 * 60 * 1000) throw new Error('先前的 GitHub 任务等待超过 15 分钟，本次登录信息尚未提交。请待任务结束后重新登录部署。');
        this.state.stage = 'verifying';
        this.note('同一仓库的先前任务仍在运行。当前登录信息仅保留在本机内存，结束后会自动接续部署。');
        await pauseFor(15000, signal);
        result = await deploy();
      }
      this.recordResult({ ...this.pendingAccount, ...result });
      const failed = FAILED.has(result.conclusion);
      this.state.stage = failed ? 'error' : 'complete';
      if (failed) this.state.error = this.resultMessage({ ...this.pendingAccount, ...result });
      this.note(this.resultMessage({ ...this.pendingAccount, ...result }), failed ? 'error' : 'info');
    });
  }

  resultMessage(result) {
    const status = result.status || result.conclusion || '';
    const evidence = evidenceFor(result);
    if (FAILED.has(result.conclusion)) return safeMessage(evidence.message || result.message || '部署已建立，但运行未通过，请查看具体结果。', this.secrets);
    if (result.result?.status === 'unverified') return '部署已建立，工作流已结束，但尚未取得该账号可核实的签到结果。请刷新或查看运行记录。';
    if (evidence.pointsAdded > 0) return `部署完成，首次签到已增加 ${evidence.pointsAdded} 积分。以后由 GitHub 定时运行。`;
    if (['already', 'already_checked'].includes(evidence.outcome) || result.conclusion === 'already_checked_in') return '部署完成，已确认今天签过到。以后由 GitHub 定时运行。';
    if (PENDING.has(status)) return '部署配置已完成，验证任务仍在排队或运行。软件会继续刷新结果。';
    if (result.conclusion === 'success') return '工作流运行成功。账号卡片会显示可核实的签到结果。';
    return safeMessage(evidence.message || result.message || '部署已建立，但首次验证未通过；请查看账号卡片中的具体结果。', this.secrets);
  }

  recordResult(result) {
    const evidence = evidenceFor(result);
    const repeated = ['already', 'already_checked'].includes(evidence.outcome);
    const conclusion = FAILED.has(result.conclusion) ? result.conclusion : result.result?.status === 'unverified' ? 'unverified' : repeated && result.conclusion === 'success' ? 'already_checked_in' : evidence.outcome === 'checked' && result.conclusion === 'success' ? 'checkin_success' : result.conclusion || result.status || 'queued';
    this.upsert({ ...result, pointsAdded: Number.isFinite(evidence.pointsAdded) ? evidence.pointsAdded : null, message: safeMessage(evidence.message || result.message || '', this.secrets), conclusion });
    this.state.currentRun = { repository: result.repository, runId: result.runId, runUrl: result.runUrl, status: result.status || 'completed', conclusion: result.conclusion || null };
  }

  findAccount(key) {
    const account = this.state.accounts.find(item => item.accountKey === key);
    if (!account) throw new Error('未找到该账号的部署记录。');
    return account;
  }

  async refreshAccount(account, signal, latest = false) {
    if (!account.repository) return;
    const result = await this.github.refresh({ repository: account.repository, accountKey: account.accountKey, ...(latest ? {} : { runId: account.runId }), signal });
    if (signal?.aborted || this.closing) return;
    this.recordResult({ ...account, ...result });
    return result;
  }

  refreshPending() {
    if (this.closing || this.state.busy) return Promise.resolve();
    if (this.backgroundTask) return this.backgroundTask;
    const activity = this.activity;
    const backgroundAbort = new AbortController();
    this.backgroundAbort = backgroundAbort;
    const signal = backgroundAbort.signal;
    const accounts = this.state.accounts.filter(a => PENDING.has(a.conclusion) || PENDING.has(a.status)).map(a => ({ ...a }));
    this.backgroundTask = (async () => {
      for (const account of accounts) {
        if (signal.aborted || this.closing || this.state.busy || activity !== this.activity) return;
        try {
          const result = await this.github.refresh({ repository: account.repository, accountKey: account.accountKey, runId: account.runId, signal });
          if (signal.aborted || this.closing || this.state.busy || activity !== this.activity) return;
          this.recordResult({ ...account, ...result });
        } catch { /* A later read may recover; never re-dispatch. */ }
      }
    })().finally(() => {
      if (this.backgroundAbort === backgroundAbort) { this.backgroundAbort = null; this.backgroundTask = null; }
    });
    return this.backgroundTask;
  }

  async shutdown() {
    this.closing = true;
    this.activity++;
    this.abort?.abort();
    this.backgroundAbort?.abort();
    this.initializeAbort?.abort();
    await Promise.allSettled([this.operationDone, this.backgroundTask, this.initializeTask].filter(Boolean));
  }

  async action(name, payload = {}) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('操作参数不正确。');
    if (name === 'cancel') { this.abort?.abort(); if (this.state.busy) { this.state.stage = 'cancelling'; this.note('正在关闭本次操作，请稍候。'); } return this.snapshot(); }
    if (name === 'connectGithub') return this.exclusive(async signal => { await this.ensureGitHub(signal, true); this.state.stage = 'idle'; this.note(`已连接 GitHub：${this.state.github.login}。`); });
    if (name === 'startDeploy') return this.startDeploy(payload);
    if (name === 'refreshRun') return this.exclusive(async signal => {
      const result = await this.refreshAccount(this.findAccount(payload.accountKey), signal, true);
      const failed = FAILED.has(result?.conclusion);
      this.state.stage = failed ? 'error' : 'complete';
      if (failed) this.state.error = this.resultMessage({ ...this.findAccount(payload.accountKey), ...result });
      this.note(failed ? this.state.error : '已读取 GitHub 上最近一次适用的运行结果。', failed ? 'error' : 'info');
    });
    if (name === 'pause') return this.exclusive(async signal => {
      const account = this.findAccount(payload.accountKey);
      if (typeof payload.paused !== 'boolean') throw new Error('暂停状态不正确。');
      await this.github.pause({ repository: account.repository, paused: payload.paused, signal });
      for (const item of this.state.accounts.filter(a => a.repository === account.repository)) item.paused = payload.paused;
      this.persist(); this.state.stage = 'complete'; this.note(payload.paused ? '此仓库的定时签到已暂停。' : '此仓库的定时签到已恢复。');
    });
    if (name === 'openRun' || name === 'openRepository') {
      const account = this.findAccount(payload.accountKey);
      const url = name === 'openRun' ? account.runUrl : `https://github.com/${account.repository}`;
      if (url) await this.options.openExternal(url);
    } else if (name === 'copyAuthCode') {
      if (this.state.authCode?.code) this.options.copyAuthCode?.(this.state.authCode.code);
    } else if (name === 'saveSettings') {
      if (this.state.busy) throw new Error('任务执行期间不能修改部署设置。');
      this.state.settings = cleanSettings({ ...this.state.settings, ...payload });
      if (this.state.browsers.some(b => b.id === payload.selectedBrowser && b.available)) this.state.selectedBrowser = payload.selectedBrowser;
      this.persist(); this.changed();
    } else throw new Error('不支持的操作。');
    return this.snapshot();
  }
}

module.exports = { Controller, safeMessage, PENDING };
