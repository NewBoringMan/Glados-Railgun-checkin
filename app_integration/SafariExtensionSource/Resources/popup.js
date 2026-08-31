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
  not_logged_in: '当前页面尚未登录，或缺少完整的两个登录 Cookie。',
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
      status.textContent = messages[reason] || (reason.startsWith('native_messaging_failed:')
        ? `Native Messaging 未连接：${reason.slice('native_messaging_failed:'.length).trim()}`
        : `未完成：${reason}`);
    }
  } catch (error) {
    status.textContent = `未完成：${error?.message || error}`;
  } finally {
    button.disabled = false;
  }
});
