import { Component, type ReactNode } from "react";

interface RendererErrorBoundaryProps {
  children: ReactNode;
  onReload?: () => void;
}

interface RendererErrorBoundaryState {
  failed: boolean;
}

export class RendererErrorBoundary extends Component<
  RendererErrorBoundaryProps,
  RendererErrorBoundaryState
> {
  state: RendererErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): RendererErrorBoundaryState {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;

    return (
      <main className="renderer-error-screen" aria-labelledby="renderer-error-title">
        <section className="renderer-error-screen__panel" role="alert">
          <span className="panel__eyebrow">界面恢复</span>
          <h1 id="renderer-error-title">界面资源加载失败</h1>
          <p>应用界面没有完整载入。重新加载不会执行任何待审批操作。</p>
          <button
            className="button button--primary"
            type="button"
            onClick={this.props.onReload ?? reloadRenderer}
          >
            重新加载
          </button>
        </section>
      </main>
    );
  }
}

function reloadRenderer(): void {
  window.location.reload();
}
