import { Check, FileKey2, KeyRound, Loader2, RefreshCw, ShieldAlert } from "lucide-react";
import { type ChangeEvent, useCallback, useEffect, useRef, useState } from "react";

import type {
  ApiError,
  CommerceFeature,
  CommerceLicenseStatus,
  CommercePlan,
  CommercePlanStatus,
  CommerceQuotaWindow,
  CommerceQuotaStatus
} from "../../../shared/types";
import type { LengrvisApiClient } from "../../lib/apiClient";

const MAX_LICENSE_FILE_BYTES = 64 * 1024;

const PLAN_LABELS: Record<CommercePlan, string> = {
  free: "免费版",
  pro: "专业版",
  max: "旗舰版"
};

const COMMERCE_ERROR_MESSAGES: Record<string, string> = {
  activation_failed: "激活失败，请稍后重试。",
  activation_service_unavailable: "激活服务暂时不可用，请稍后重试。",
  activation_malformed_response: "激活服务返回的数据不完整。",
  activation_unconfigured: "尚未配置激活服务器。",
  activation_url_invalid: "激活服务器地址无效。",
  activation_https_required: "激活服务器必须使用 HTTPS。",
  activation_server_unconfigured: "激活服务器配置不完整。",
  activation_storage_unavailable: "激活存储目录不可用。",
  activation_device_identity_unavailable: "暂时无法读取本机设备身份。",
  activation_key_required: "请输入订阅授权码。",
  activation_key_invalid: "订阅授权码无效。",
  activation_key_not_found: "订阅授权码不存在或已失效。",
  activation_rate_limited: "激活尝试次数过多，请稍后再试。",
  activation_device_required: "设备标识不能为空。",
  activation_device_invalid: "设备标识无效。",
  activation_device_limit: "已达到该订阅允许绑定的设备数量。",
  activation_device_not_found: "未找到该激活设备。",
  activation_device_mismatch: "设备与该许可证不匹配。",
  activation_device_rebind_requires_unbind: "该设备指纹已绑定到其他激活记录，请先在后台解绑旧设备。",
  activation_device_fingerprint_invalid: "设备指纹无效。",
  activation_device_fingerprint_mismatch: "设备指纹与本次激活记录不一致。",
  activation_device_profile_mismatch: "设备证明与本次设备指纹不一致。",
  activation_fingerprint_required: "新设备激活必须提交设备指纹。",
  activation_device_proof_weak: "设备绑定证明强度不足。",
  activation_nonce_required: "激活请求缺少安全随机数。",
  activation_nonce_invalid: "激活请求安全随机数无效。",
  activation_nonce_mismatch: "激活服务返回的许可证不是本次请求的结果。",
  license_token_required: "许可证令牌不能为空。",
  license_token_too_large: "许可证令牌过大。",
  license_public_key_missing: "当前构建未配置许可证验签公钥。",
  license_signature_mismatch: "许可证签名不匹配。",
  license_signature_invalid: "许可证签名无效。",
  license_expired: "许可证已过期。",
  license_revoked: "许可证已被吊销。",
  license_revocation_required: "商业离线许可证缺少签名吊销清单。",
  license_revocation_stale: "商业离线许可证的吊销清单已过期。",
  license_revocation_time_invalid: "商业离线许可证的吊销清单时间无效。",
  license_revocation_id_missing: "商业离线许可证缺少可吊销的许可证编号。",
  license_device_mismatch: "许可证绑定到另一台设备。",
  license_device_unverified: "无法核验许可证绑定的设备。",
  license_device_proof_missing: "激活许可证缺少强设备绑定证明。",
  license_device_proof_mismatch: "激活许可证设备绑定证明与设备指纹不一致。",
  license_device_proof_weak: "激活许可证设备绑定证明强度不足。",
  license_managed_externally: "当前许可证由部署配置托管，不能在应用内替换。",
  license_storage_unavailable: "许可证存储目录不可用。",
  license_storage_failed: "无法保存许可证。",
  revocation_data_invalid: "吊销数据不可信，付费能力已停用。",
  subscription_required: "该许可证不是订阅许可证。",
  subscription_mismatch: "许可证订阅与激活记录不一致。",
  subscription_past_due: "订阅已逾期，请处理付款后重试。",
  subscription_canceled: "订阅已取消。",
  subscription_expired: "订阅已过期。",
  subscription_revoked: "订阅已被撤销。",
  subscription_confirmation_required: "订阅许可证需要重新联网确认。",
  subscription_confirmation_failed: "订阅联网确认失败，已回退到免费能力。",
  validation_error: "请求参数校验失败。"
};

const FEATURE_LABELS: Record<CommerceFeature, string> = {
  local_read_only: "本机只读",
  basic_tasks: "基础任务",
  cloud_quota: "云端额度",
  document_ai: "文档 AI",
  scheduling: "定时任务",
  remote_view: "手机查看",
  remote_control: "远程输入",
  audit_export: "审计导出",
  policy_management: "策略管控",
  private_deployment: "私有部署"
};

export function CommercePanel({ api }: { api: LengrvisApiClient }) {
  const [plan, setPlan] = useState<CommercePlanStatus | null>(null);
  const [license, setLicense] = useState<CommerceLicenseStatus | null>(null);
  const [quota, setQuota] = useState<CommerceQuotaStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [installing, setInstalling] = useState(false);
  const [activating, setActivating] = useState(false);
  const [activationKey, setActivationKey] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [planResponse, licenseResponse, quotaResponse] = await Promise.all([
        api.getCommercePlan(),
        api.getCommerceLicense(),
        api.getCommerceQuota()
      ]);
      if (!planResponse.ok || !planResponse.data) {
        throw new Error(responseErrorMessage(planResponse.error, "无法读取当前套餐"));
      }
      if (!licenseResponse.ok || !licenseResponse.data) {
        throw new Error(responseErrorMessage(licenseResponse.error, "无法读取授权状态"));
      }
      if (!quotaResponse.ok || !quotaResponse.data) {
        throw new Error(responseErrorMessage(quotaResponse.error, "无法读取额度状态"));
      }
      setPlan(planResponse.data);
      setLicense(licenseResponse.data);
      setQuota(quotaResponse.data);
    } catch (refreshError) {
      setError(readableMessage(refreshError, "无法读取套餐与授权状态"));
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const importLicense = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setMessage("");
    setError("");
    if (file.size > MAX_LICENSE_FILE_BYTES) {
      setError("许可证文件过大，请选择官方提供的 .key 或 .lic 文件。");
      return;
    }
    setInstalling(true);
    try {
      const token = (await file.text()).trim();
      if (!token) throw new Error("许可证文件为空");
      const response = await api.installCommerceLicense(token);
      if (!response.ok || !response.data?.active) {
        throw new Error(responseErrorMessage(response.error, "许可证未通过验签或已过期"));
      }
      setMessage(`已启用 ${PLAN_LABELS[response.data.plan ?? "free"]} 授权。`);
      await refresh();
    } catch (installError) {
      setError(readableMessage(installError, "许可证导入失败"));
    } finally {
      setInstalling(false);
    }
  };

  const activateSubscription = async () => {
    const key = activationKey.trim();
    if (!key) {
      setError("请输入订阅授权码。");
      return;
    }
    setMessage("");
    setError("");
    setActivating(true);
    try {
      const response = await api.activateCommerceLicense(key, "desktop");
      if (!response.ok || !response.data?.active) {
        throw new Error(responseErrorMessage(response.error, "订阅授权码未通过服务器确认"));
      }
      setActivationKey("");
      setMessage(`已激活 ${PLAN_LABELS[response.data.plan ?? "free"]} 订阅。`);
      await refresh();
    } catch (activationError) {
      setError(readableMessage(activationError, "订阅激活失败"));
    } finally {
      setActivating(false);
    }
  };

  const canImport = Boolean(license?.verifierConfigured && license.managedBy !== "environment");
  const canActivate = Boolean(license?.verifierConfigured && license.managedBy !== "environment");
  const enabledFeatures = plan
    ? (Object.entries(plan.features) as Array<[CommerceFeature, boolean]>).filter(([, enabled]) => enabled)
    : [];

  return (
    <fieldset className="mcp-servers commerce-settings settings-grid__full">
      <legend>套餐与授权</legend>
      <div className="commerce-settings__header">
        <div>
          <span className="commerce-settings__eyebrow">当前套餐</span>
          <strong>{plan ? PLAN_LABELS[plan.plan] : "读取中"}</strong>
          <small>{licenseSummary(license)}</small>
        </div>
        <div className="commerce-settings__actions">
          <input
            ref={fileInputRef}
            className="commerce-settings__file"
            type="file"
            accept=".key,.lic,text/plain"
            onChange={(event) => void importLicense(event)}
          />
          <button
            type="button"
            className="button button--secondary"
            onClick={() => fileInputRef.current?.click()}
            disabled={!canImport || installing}
            title={canImport ? "导入官方签发的离线许可证" : licenseImportUnavailableReason(license)}
          >
            {installing ? <Loader2 className="settings-spinner" size={15} aria-hidden="true" /> : <FileKey2 size={15} aria-hidden="true" />}
            导入许可证
          </button>
          <button
            type="button"
            className="button button--ghost"
            onClick={() => void refresh()}
            disabled={loading || installing}
            title="刷新套餐与授权状态"
            aria-label="刷新套餐与授权状态"
          >
            <RefreshCw className={loading ? "settings-spinner" : ""} size={15} aria-hidden="true" />
          </button>
        </div>
      </div>

      <form
        className="commerce-settings__activation"
        onSubmit={(event) => {
          event.preventDefault();
          void activateSubscription();
        }}
      >
        <label>
          <span>订阅授权码</span>
          <input
            type="password"
            value={activationKey}
            onChange={(event) => setActivationKey(event.target.value)}
            autoComplete="off"
            spellCheck={false}
            placeholder="输入免费版 / 专业版 / 旗舰版订阅授权码"
            disabled={!canActivate || activating || installing}
          />
        </label>
        <button
          type="submit"
          className="button button--secondary"
          disabled={!canActivate || activating || installing || !activationKey.trim()}
          title={canActivate ? "向激活服务器确认订阅 key" : licenseImportUnavailableReason(license)}
        >
          {activating ? <Loader2 className="settings-spinner" size={15} aria-hidden="true" /> : <KeyRound size={15} aria-hidden="true" />}
          激活
        </button>
      </form>

      {enabledFeatures.length ? (
        <div className="commerce-settings__features" aria-label="当前已启用能力">
          {enabledFeatures.map(([feature]) => (
            <span key={feature}>
              <Check size={13} aria-hidden="true" />
              {FEATURE_LABELS[feature]}
              {plan?.highRiskFeatures.includes(feature) ? <em>逐次审批</em> : null}
            </span>
          ))}
        </div>
      ) : null}

      <div className="commerce-settings__facts">
        <span>
          <small>云端额度</small>
          <strong>{quotaSummary(quota)}</strong>
        </span>
        <span>
          <small>授权主体</small>
          <strong>{license?.subject || "个人免费使用"}</strong>
        </span>
        <span>
          <small>有效期</small>
          <strong>{licenseExpiry(license)}</strong>
        </span>
        {license?.licenseId ? (
          <span>
            <small>许可证编号</small>
            <strong>{license.licenseId}</strong>
          </span>
        ) : null}
      </div>

      {license?.state === "verifier_unconfigured" ? (
        <p className="commerce-settings__warning">
          <ShieldAlert size={15} aria-hidden="true" />
          当前构建未配置许可证验签公钥，不能接受付费许可证。
        </p>
      ) : null}
      {license?.state === "revocation_data_invalid" ? (
        <p className="commerce-settings__warning">
          <ShieldAlert size={15} aria-hidden="true" />
          吊销清单未通过验签，付费授权已按不可信状态停用。
        </p>
      ) : null}
      {message ? <p className="settings-status" role="status">{message}</p> : null}
      {error ? <p className="field-error" role="alert">{error}</p> : null}
    </fieldset>
  );
}

function licenseSummary(license: CommerceLicenseStatus | null): string {
  if (!license) return "正在核验本机授权";
  if (license.planEnvIgnored && license.requestedEnvPlan && license.requestedEnvPlan !== "free") {
    return `${PLAN_LABELS[license.requestedEnvPlan]} 环境变量未提供有效许可证，已回退到免费能力`;
  }
  if (license.state === "active") {
    if (license.subscriptionStatus) {
      return license.cancelAtPeriodEnd ? "订阅已取消，将在周期结束后停用" : "订阅已激活";
    }
    return license.managedBy === "environment" ? "由组织部署策略管理" : "本机许可证已验签";
  }
  if (license.state === "expired") return "许可证已过期，已回退到免费能力";
  if (license.state === "revoked") return "许可证已被吊销，已回退到免费能力";
  if (license.state === "device_mismatch") return "许可证绑定到另一台设备，已回退到免费能力";
  if (license.state === "device_unverified") return "许可证已绑定设备，但本机暂时无法核验设备身份，已回退到免费能力";
  if (license.state === "subscription_inactive") return "订阅状态不可用，已回退到免费能力";
  if (license.state === "revocation_data_invalid") return "吊销数据不可信，付费能力已停用";
  if (license.state === "invalid") return "许可证无效，已回退到免费能力";
  if (license.state === "verifier_unconfigured") return "发行配置不完整";
  return "未导入付费许可证";
}

function licenseImportUnavailableReason(license: CommerceLicenseStatus | null): string {
  if (!license) return "授权状态尚未加载";
  if (license.managedBy === "environment") return "该授权由组织部署策略管理";
  if (!license.verifierConfigured) return "当前构建未配置许可证验签公钥";
  return "暂时无法导入许可证";
}

function quotaSummary(quota: CommerceQuotaStatus | null): string {
  if (!quota) return "读取中";
  if (quota.unlimited) return "不设产品内上限";
  const windows =
    quota.windows.length > 0
      ? quota.windows
      : [
          {
            key: `${quota.windowHours}h`,
            windowHours: quota.windowHours,
            limits: quota.limits,
            usage: quota.usage,
            exceeded: quota.exceeded
          }
        ];
  const summary = windows.map(formatQuotaWindow).join("；");
  if (!summary) return "未配置额度";
  return quota.enforced ? summary : `${summary}（未强制）`;
}

function formatQuotaWindow(window: CommerceQuotaWindow): string {
  const used = window.usage?.totalTokens ?? 0;
  const limit = window.limits.totalTokens;
  if (limit === null) return `${formatQuotaWindowLabel(window.windowHours)} 不设上限`;
  const suffix = window.exceeded.length > 0 ? " 已达上限" : "";
  return `${formatQuotaWindowLabel(window.windowHours)} ${formatNumber(used)} / ${formatNumber(limit)} 令牌${suffix}`;
}

function formatQuotaWindowLabel(windowHours: number): string {
  if (windowHours === 168) return "7天";
  if (windowHours % 24 === 0) return `${windowHours / 24}天`;
  return `${windowHours}小时`;
}

function licenseExpiry(license: CommerceLicenseStatus | null): string {
  if (!license?.active) return "不适用";
  if (license.renewsAt) {
    const renewsAt = new Date(license.renewsAt);
    const value = Number.isNaN(renewsAt.getTime()) ? license.renewsAt : renewsAt.toLocaleDateString("zh-CN");
    return license.cancelAtPeriodEnd ? `${value} 到期` : `${value} 续费`;
  }
  if (!license.expiresAt) return "长期有效";
  const date = new Date(license.expiresAt);
  return Number.isNaN(date.getTime()) ? license.expiresAt : date.toLocaleDateString("zh-CN");
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(value);
}

function readableMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && hasChinese(error.message)) return error.message;
  return fallback;
}

function responseErrorMessage(error: ApiError | undefined, fallback: string): string {
  if (!error) return fallback;
  if (error.code && COMMERCE_ERROR_MESSAGES[error.code]) return COMMERCE_ERROR_MESSAGES[error.code];
  if (hasChinese(error.message)) return error.message;
  return fallback;
}

function hasChinese(value: unknown): boolean {
  return typeof value === "string" && /[\u4e00-\u9fff]/.test(value);
}
