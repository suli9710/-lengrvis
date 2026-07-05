import { describe, expect, it } from "vitest";

import { mapContextUsage, mapLlmCostSummary, mapLlmHealth, mapLlmProfile } from "./llmContextMappers";

describe("llm and context mappers", () => {
  it("maps LLM health with profile defaults and retry circuit details", () => {
    const health = mapLlmHealth({
      active: {
        available: true,
        degraded: false,
        provider: "openai_compatible",
        model: "gpt-test",
        profile: {
          provider_name: "openai_compatible",
          model: "gpt-test",
          location: "cloud",
          active_backend: "responses",
          capabilities: {
            tools: true,
            structured_json: false,
            responses_api: true,
            cloud: true
          },
          model_profile: {
            context_window: 128000,
            max_output_tokens: 4096,
            known: true,
            family: "gpt"
          }
        }
      },
      retry: {
        max_retries: 3,
        backoff_seconds: 0.5,
        circuit_failure_threshold: 5,
        circuit_cooldown_seconds: 30,
        circuit: {
          state: "open",
          failures: 2,
          retry_after_seconds: 15
        }
      }
    });

    expect(health).toMatchObject({
      active: {
        available: true,
        provider: "openai_compatible",
        model: "gpt-test",
        profile: {
          providerName: "openai_compatible",
          model: "gpt-test",
          location: "cloud",
          activeBackend: "responses",
          capabilities: {
            tools: true,
            structuredJson: false,
            responsesApi: true,
            cloud: true
          },
          modelProfile: {
            contextWindow: 128000,
            maxOutputTokens: 4096,
            known: true,
            family: "gpt"
          }
        }
      },
      retry: {
        maxRetries: 3,
        backoffSeconds: 0.5,
        circuitFailureThreshold: 5,
        circuitCooldownSeconds: 30,
        circuit: {
          state: "open",
          failures: 2,
          retryAfterSeconds: 15
        }
      }
    });
  });

  it("maps profile and cost summary fallbacks", () => {
    expect(mapLlmProfile({ model_profile: { model: "fallback-model" } })).toMatchObject({
      providerName: "",
      model: "fallback-model",
      wireApi: "chat_completions",
      capabilities: {
        structuredJson: true
      },
      modelProfile: {
        model: "fallback-model",
        contextWindow: 0,
        maxOutputTokens: 0,
        known: false
      }
    });

    expect(
      mapLlmCostSummary({
        calls: 2,
        prompt_tokens: 100,
        completion_tokens: 20,
        total_tokens: 120,
        total_cost_usd: null,
        by_model: [{ provider: "local", model: "llama", calls: 1, total_cost_usd: undefined }]
      })
    ).toMatchObject({
      windowHours: 24,
      calls: 2,
      promptTokens: 100,
      completionTokens: 20,
      totalTokens: 120,
      totalCostUsd: null,
      byModel: [
        {
          provider: "local",
          model: "llama",
          calls: 1,
          totalCostUsd: 0
        }
      ]
    });
  });

  it("maps context usage health, projection, and lineage fallbacks", () => {
    const usage = mapContextUsage({
      used_tokens: 80,
      effective_context_window: 100,
      warning: {
        token_count: 80,
        threshold: 70,
        is_above_warning_threshold: true
      },
      projection: {
        original_count: 10,
        projected_count: 7,
        original_tokens: 80,
        projected_tokens: 50,
        strategy: "summarize_tail",
        boundary_id: "b1",
        retained_tail_message_ids: ["m1", "m2"],
        summary: {
          enabled: true,
          compacted: true,
          adjustments: ["trimmed"]
        }
      },
      health: {
        status: "managed",
        severity: "warning",
        reason: "managed by projection"
      },
      lineage: {
        task_id: "task_1",
        message_roles: { user: "2", assistant: 3 },
        projection: {
          source: "projection",
          strategy: "summarize_tail",
          boundary_id: "b1"
        }
      }
    });

    expect(usage).toMatchObject({
      usedTokens: 80,
      freeTokens: 20,
      health: {
        status: "managed",
        severity: "warning",
        usedPercent: 80,
        projectedTokens: 50,
        projectedPercent: 50
      },
      projection: {
        enabled: true,
        strategy: "summarize_tail",
        compacted: true,
        originalTokens: 80,
        projectedTokens: 50,
        tokensSaved: 30,
        messagesRemoved: 3,
        adjustments: ["trimmed"]
      },
      lineage: {
        taskId: "task_1",
        messageRoles: { user: 2, assistant: 3 },
        projection: {
          source: "projection",
          boundaryId: "b1",
          retainedTailCount: 2
        }
      }
    });
  });
});
