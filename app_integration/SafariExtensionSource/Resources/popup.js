'use strict';

const api = globalThis.browser || globalThis.chrome;
const button = document.getElementById('send');
const status = document.getElementById('status');

const messages = {
  no_pending: 'GLaDOS Account Center尚未启动 Safari 读取流程。',
  invalid_pending: '本次读取会话无效，请在GLaDOS Account Center中重新选择 Safari。',
  expired_pending: '本次读取会话已过期，请重新启动 Safari 读取流程。',
  no_active_tab: '没有检测到当前 Safari 标签页。',
  invalid_page_url: '当前页面地址无效。',
  not_glados_page: '请切换到 glados.cloud 或 railgun.info 页面。',
  not_logged_in: '当前页面尚未登录，或缺少完整的 gld / koa 签名会话。请手动登录后再发送。',
  origin_mismatch: '当前标签页与本次手动读取的原域名或原标签页不一致，请回到最初选择的页面。',
  invalid_user_agent: '未取得实际浏览器信息，请保持原登录页面后重新发送。',
  context_changed: '读取期间浏览器信息发生变化，请手动重新读取。',
  context_unavailable: '无法读取当前登录页面的浏览器信息，请刷新原页面后手动重新发送。',
  ambiguous_cookies: '当前域名存在重名 Cookie，无法安全确定会话，请手动重新登录后再读取。',
  cookie_scope_mismatch: '各接口适用的登录 Cookie 不一致，当前保存格式无法安全复用，已停止且未保存。请保留原网页登录。',
  capture_in_progress: '本次手动读取正在进行中，请稍候。',
  native_messaging_failed: 'Safari Native Messaging 未连接，请确认应用内嵌的扩展已启用。',
  native_bridge_rejected: 'GLaDOS Account Center拒绝了此次数据。',
};

button.addEventListener('click', async () => {
  button.disabled = true;
  status.textContent = '正在读取并通过 Safari Native Messaging 发送……';
  try {
    const result = await api.runtime.sendMessage({ type: 'MANUAL_CAPTURE' });
    if (result?.ok) {
      status.textContent = '发送成功。现在回到 GLaDOS Account Center继续。';
    } else {
      const reason = String(result?.reason || 'unknown');
      status.textContent = messages[reason] || '本次手动读取未完成，请回到应用查看提示。';
    }
  } catch {
    status.textContent = '扩展连接暂时不可用，请回到应用重新开始手动读取。';
  } finally {
    button.disabled = false;
  }
});
