import { fetchJson } from './util.js';

function resolveConfig() {
  const env = process.env;
  if (env.LLM_API_KEY) {
    return {
      provider: 'custom',
      base: (env.LLM_BASE_URL || 'https://api.openai.com/v1').replace(/\/+$/, ''),
      key: env.LLM_API_KEY,
      model: env.LLM_MODEL || 'gpt-4o-mini',
    };
  }
  if (env.ARK_API_KEY) {
    return {
      provider: 'ark',
      base: 'https://ark.cn-beijing.volces.com/api/v3',
      key: env.ARK_API_KEY,
      model: env.ARK_MODEL || 'doubao-seed-1-6-250615',
    };
  }
  if (env.OPENAI_API_KEY) {
    return {
      provider: 'openai',
      base: (env.OPENAI_BASE_URL || 'https://api.openai.com/v1').replace(/\/+$/, ''),
      key: env.OPENAI_API_KEY,
      model: env.OPENAI_MODEL || 'gpt-4o-mini',
    };
  }
  return null;
}

export function llmStatus() {
  const cfg = resolveConfig();
  return {
    enabled: Boolean(cfg),
    provider: cfg?.provider || 'builtin-rules',
    model: cfg?.model || null,
  };
}

export async function llmChat(messages, { temperature = 0.4, maxTokens = 900, timeout = 45000 } = {}) {
  const cfg = resolveConfig();
  if (!cfg) return null;
  try {
    const json = await fetchJson(`${cfg.base}/chat/completions`, {
      method: 'POST',
      timeout,
      retries: 0,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${cfg.key}`,
      },
      body: JSON.stringify({
        model: cfg.model,
        messages,
        temperature,
        max_tokens: maxTokens,
      }),
    });
    const content = json?.choices?.[0]?.message?.content;
    return typeof content === 'string' && content.trim() ? content.trim() : null;
  } catch (err) {
    console.warn('[llm] request failed:', err.message);
    return null;
  }
}
