// Cloudflare Worker: 接收邮件写入 KV，提供 /get-otp 接口
// 这是已在生产环境验证的 v5 版本
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/health') {
      return new Response(JSON.stringify({status:'ok',version:'v5'}), {headers:{'Content-Type':'application/json'}});
    }
    if (url.pathname === '/get-otp') {
      const email = url.searchParams.get('email');
      if (!email) return new Response('missing email', {status:400});
      const val = await env.CODEX_OTP.get(email);
      return new Response(JSON.stringify({otp: val}), {headers:{'Content-Type':'application/json'}});
    }
    return new Response('not found', {status:404});
  },
  async email(message, env) {
    const to = message.to;
    const text = await new Response(message.raw).text();
    const match = text.match(/\b(\d{6})\b/);
    if (match) {
      await env.CODEX_OTP.put(to, match[1], {expirationTtl: 300});
    }
    await message.forward('noreply@example.com').catch(()=>{});
  }
};
