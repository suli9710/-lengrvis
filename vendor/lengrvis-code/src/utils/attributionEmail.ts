const MODEL_EMAIL_MAP: Array<{ keywords: string[]; email: string }> = [
  { keywords: ['claude'], email: 'noreply@anthropic.com' },
  // 由于找不到他们的邮箱和头像, 所以改为了使用我们的邮箱先记录, 后续官方有 github 能用的邮箱可以替换
  // github 组织是不能用 co author 的
  {
    keywords: ['gpt', 'dall-e', 'o1-', 'o3-', 'o4-'],
    email: 'openai@lengrvis-code-best.win',
  },
  { keywords: ['gemini'], email: 'google-gemini@lengrvis-code-best.win' },
  { keywords: ['grok'], email: 'xai-org@lengrvis-code-best.win' },
  { keywords: ['glm'], email: 'zai-org@lengrvis-code-best.win' },
  { keywords: ['deepseek'], email: 'deepseek-ai@lengrvis-code-best.win' },
  { keywords: ['qwen'], email: 'QwenLM@lengrvis-code-best.win' },
  { keywords: ['minimax'], email: 'MiniMax-AI@lengrvis-code-best.win' },
  { keywords: ['mimo'], email: 'XiaomiMiMo@lengrvis-code-best.win' },
  { keywords: ['kimi'], email: 'MoonshotAI@lengrvis-code-best.win' },
]

export function getAttributionEmail(modelName: string): string {
  const lower = modelName.toLowerCase()
  for (const { keywords, email } of MODEL_EMAIL_MAP) {
    if (keywords.some(kw => lower.includes(kw))) {
      return email
    }
  }
  return 'noreply@anthropic.com'
}
