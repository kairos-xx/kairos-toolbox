"use strict";
/**
 * Read an index-signature property without dot/bracket lint conflicts.
 * @param {object | null | undefined} target Source object.
 * @param {PropertyKey} attribute Property key.
 * @returns {*} Property value.
 */
const getAttribute = (target, attribute) => target == null ? undefined : Reflect.get(target, attribute);
const fs = require("node:fs/promises");
const path = require("node:path");
const vscode = require("vscode");
const { ActivityService } = require("./lib/activity-service");
const { DevinClient } = require("./lib/devin-client");
const { FileService } = require("./lib/file-service");
const { GatewayServer } = require("./lib/gateway-server");
const { NgrokManager } = require("./lib/ngrok-manager");
const { ProjectRegistry } = require("./lib/project-registry");
const { ProviderService } = require("./lib/provider-service");
const { SecurityService } = require("./lib/security");
const { TerminalManager } = require("./lib/shell-session");
/** Coordinates services, the gateway, and the webview panel. */
class ExtensionController {
  /**
   * Creates an instance of ExtensionController.
   * @param {import("vscode").ExtensionContext} context The extension context.
   */
  constructor(context) {
    /** @type {import("vscode").ExtensionContext} */
    this.context = context;
    /** @type {import("vscode").WebviewPanel | null} */
    this.panel = null;
    /** @type {Map<string, object>} */
    this.pairing = new Map();
    /** @type {import("vscode").Disposable[]} */
    this.disposables = [];
    /** @type {InstanceType<typeof ProjectRegistry>} */
    this.registry = new ProjectRegistry(vscode);
    /** @type {import("vscode").StatusBarItem} */
    this.status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 96);
    const config = ExtensionController.configuration();
    /** @type {InstanceType<typeof SecurityService>} */
    this.security = new SecurityService(config.sessionHours);
    /** @type {InstanceType<typeof FileService>} */
    this.files = new FileService(config.maxBytes, config.excludes);
    /** @type {InstanceType<typeof DevinClient>} */
    this.devin = new DevinClient(
      config.orgId,
      async () => this.context.secrets.get("devinRemote.devinToken")
    );
    /** @type {InstanceType<typeof ProviderService>} */
    this.providers = new ProviderService(
      {
        openAIBaseUrl: config.openAIBaseUrl,
        openAIModel: config.openAIModel,
        anthropicModel: config.anthropicModel
      },
      /** @type {(provider: string) => Promise<string | undefined>} */
      async provider => this.context.secrets.get(`devinRemote.provider.${provider}`)
    );
    /** @type {InstanceType<typeof TerminalManager>} */
    this.terminals = new TerminalManager(config.shell);
    /** @type {InstanceType<typeof ActivityService>} */
    this.activity = new ActivityService();
    /** @type {InstanceType<typeof GatewayServer>} */
    this.gateway = new GatewayServer({
      registry: this.registry,
      security: this.security,
      files: this.files,
      devin: this.devin,
      providers: this.providers,
      terminals: this.terminals,
      activity: this.activity,
      assetsRoot: path.join(this.context.extensionPath, "media", "web")
    });
    /** @type {InstanceType<typeof NgrokManager>} */
    this.ngrok = new NgrokManager(
      config.ngrokCommand,
      async () => this.context.secrets.get("devinRemote.ngrokToken")
    );
    this.gateway.on("change", () => {
      this.updateStatus();
      this.postState();
    });
    this.ngrok.on("change", () => {
      this.updateStatus();
      this.postState();
    });
    this.registry.on("projects", () => this.postState());
    this.registry.on("active", () => this.postState());
    this.registry.on("diagnostics", projectId => {
      this.gateway.notifyProject(projectId, "diagnostics", {
        items: this.registry.diagnostics(projectId)
      });
    });
    this.status.command = "devinRemote.openProjectPanel";
    this.status.name = "Devin Remote";
    this.status.tooltip = "Open Devin Remote project access";
    this.status.show();
    this.register();
    this.updateStatus();
  }
  static configuration = () => {
    const config = vscode.workspace.getConfiguration("devinRemote");
    return {
      autoStart: config.get("gateway.autoStart", true),
      port: config.get("gateway.port", 0),
      ngrokAutoStart: config.get("ngrok.autoStart", true),
      ngrokCommand: config.get("ngrok.command", "ngrok"),
      orgId: config.get("devin.organizationId", ""),
      maxBytes: config.get("files.maxBytes", 4194304),
      excludes: config.get("files.excludes", []),
      shell: config.get("terminal.shell", ""),
      sessionHours: config.get("security.sessionHours", 12),
      openAIBaseUrl: config.get(
        "providers.openAICompatible.baseUrl",
        "https://api.openai.com/v1"
      ),
      openAIModel: config.get("providers.openAICompatible.model", ""),
      anthropicModel: config.get("providers.anthropic.model", "")
    };
  };
  /** Registers. */
  register() {
    const command = /** @type {(name: string, callback: () => unknown) => number} */ (
      (name, callback) => this.context.subscriptions.push(
        vscode.commands.registerCommand(name, callback)
      )
    );
    command("devinRemote.openProjectPanel", () => this.openPanel());
    command("devinRemote.startGateway", () => this.startGateway());
    command("devinRemote.stopGateway", () => this.stopGateway());
    command("devinRemote.setDevinCredentials", () => this.setDevinCredentials());
    command("devinRemote.setProviderCredentials", () => this.setProviderCredentials());
    command("devinRemote.setNgrokToken", () => this.setNgrokToken());
    this.context.subscriptions.push(
      this.status,
      this.registry,
      { dispose: () => this.dispose() }
    );
    if (ExtensionController.configuration().autoStart) this.startGateway();
  }
  /** Starts gateway. */
  async startGateway() {
    try {
      const port = await this.gateway.start(ExtensionController.configuration().port);
      if (ExtensionController.configuration().ngrokAutoStart) await this.ngrok.start(port);
      this.updateStatus();
      await this.postState();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.updateStatus(message);
      vscode.window.showErrorMessage(`Devin Remote: ${message}`);
    }
  }
  /** Stops gateway. */
  async stopGateway() {
    this.ngrok.stop();
    await this.gateway.stop();
    this.updateStatus();
    await this.postState();
  }
  /**
   * Updates status.
   * @param {string} error Error.
   */
  updateStatus(error = "") {
    const tunnel = this.ngrok?.snapshot() ?? { state: "stopped" };
    const gateway = this.gateway?.snapshot() ?? { running: false };
    if (error || getAttribute(tunnel, "state") === "error") {
      this.status.text = "$(warning) Devin Remote";
      this.status.tooltip = error || tunnel.error || "Remote access error";
      return;
    }
    if (getAttribute(tunnel, "state") === "online") {
      this.status.text = "$(remote) Devin Remote";
      this.status.tooltip = "Remote access is online · Click for project QR";
      return;
    }
    if (gateway.running || getAttribute(tunnel, "state") === "starting") {
      this.status.text = "$(sync~spin) Devin Remote";
      this.status.tooltip = "Starting remote access…";
      return;
    }
    this.status.text = "$(circle-slash) Devin Remote";
    this.status.tooltip = "Remote access is stopped · Click to configure";
  }
  /**
   * Returns the project for url.
   * @param {*} projectId ProjectId.
   * @returns {*} The resulting value.
   */
  projectUrl(projectId) {
    const base = this.ngrok.snapshot().publicUrl;
    return base ? `${base}/p/${projectId}/` : "";
  }
  /**
   * Returns the current state of.
   * @returns {Promise<Record<string, unknown>>} The resulting value.
   */
  async state() {
    const [devinToken, openAIKey, anthropicKey, ngrokToken] = await Promise.all([
      this.context.secrets.get("devinRemote.devinToken"),
      this.context.secrets.get("devinRemote.provider.openai-compatible"),
      this.context.secrets.get("devinRemote.provider.anthropic"),
      this.context.secrets.get("devinRemote.ngrokToken")
    ]);
    const active = this.registry.active();
    const agents = this.gateway?.agentCache ?? [];
    const activeAgents = agents.filter(session => /running|working|active/u.test(
      String(getAttribute(session, "status") ?? getAttribute(session, "state") ?? "").toLowerCase()
    ));
    return {
      gateway: this.gateway.snapshot(),
      tunnel: this.ngrok.snapshot(),
      projects: this.registry.publicList().map(project => ({
        ...project,
        url: this.projectUrl(project.id),
        pairing: this.pairing.get(project.id) ?? null
      })),
      activeProjectId: active?.id ?? "",
      metrics: {
        agents: agents.length,
        activeAgents: activeAgents.length,
        acu: agents.reduce((total, session) => total + Number(
          getAttribute(session, "acu_usage") ?? getAttribute(session, "acu_used") ?? getAttribute(session, "consumed_acu") ?? 0
        ), 0),
        pairedDevices: active ? this.security.listProjectSessions(active.id).length : 0
      },
      connections: {
        devin: Boolean(ExtensionController.configuration().orgId && devinToken),
        openAI: Boolean(openAIKey && ExtensionController.configuration().openAIModel),
        anthropic: Boolean(anthropicKey && ExtensionController.configuration().anthropicModel),
        ngrokToken: Boolean(ngrokToken)
      }
    };
  }
  /** Posts state. */
  async postState() {
    if (this.panel)
      await this.panel.webview.postMessage({ type: "state", state: await this.state() });
  }
  /** Opens panel. */
  async openPanel() {
    if (this.panel) {
      this.panel.reveal(vscode.ViewColumn.Active);
      await this.postState();
      return;
    }
    this.panel = vscode.window.createWebviewPanel(
      "devinRemote.project",
      "Devin Remote",
      vscode.ViewColumn.Active,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, "media")]
      }
    );
    this.panel.webview.html = await this.panelHtml(this.panel.webview);
    this.panel.onDidDispose(() => {
      this.panel = null;
    });
    this.panel.webview.onDidReceiveMessage(message => this.handlePanelMessage(message));
    await this.postState();
  }
  /**
   * Panel html.
   * @param {import("vscode").Webview} webview Webview.
   * @returns {Promise<string>} The resulting value.
   */
  async panelHtml(webview) {
    const root = vscode.Uri.joinPath(this.context.extensionUri, "media");
    const html = await fs.readFile(path.join(this.context.extensionPath, "media", "panel", "index.html"), "utf8");
    return html
      .replaceAll("{{CSP_SOURCE}}", webview.cspSource)
      .replaceAll(
        "{{PANEL_CSS}}",
        webview.asWebviewUri(vscode.Uri.joinPath(root, "panel", "panel.css")).toString()
      )
      .replaceAll(
        "{{PANEL_JS}}",
        webview.asWebviewUri(vscode.Uri.joinPath(root, "panel", "panel.js")).toString()
      )
      .replaceAll(
        "{{QR_JS}}",
        webview.asWebviewUri(vscode.Uri.joinPath(root, "shared", "qr.js")).toString()
      );
  }
  /**
   * Handles panel message.
   * @param {*} message Message.
   * @throws {Error} When the operation cannot complete.
   */
  async handlePanelMessage(message) {
    try {
      const projectId = String(message.projectId ?? "");
      const project = projectId ? this.registry.get(projectId) : this.registry.active();
      switch (message.action) {
        case "pair": {
          if (!project) throw new Error("Open a workspace folder first.");
          const pairing = this.security.createPairing(project.id);
          this.pairing.set(project.id, pairing);
          await this.postState();
          break;
        }
        case "open": {
          if (!project) throw new Error("Project not found.");
          const url = this.projectUrl(project.id);
          if (!url) throw new Error("The gateway is not online yet.");
          await vscode.env.openExternal(vscode.Uri.parse(url));
          break;
        }
        case "copy": {
          if (!project) throw new Error("Project not found.");
          const url = this.projectUrl(project.id);
          if (!url) throw new Error("The gateway is not online yet.");
          await vscode.env.clipboard.writeText(url);
          vscode.window.showInformationMessage("Project URL copied.");
          break;
        }
        case "revoke":
          if (project) this.security.revokeProject(project.id);
          break;
        case "set-devin":
          await this.setDevinCredentials();
          break;
        case "set-openai":
          await this.setProviderCredentials("openai-compatible");
          break;
        case "set-anthropic":
          await this.setProviderCredentials("anthropic");
          break;
        case "set-ngrok":
          await this.setNgrokToken();
          break;
        case "toggle-tunnel":
          if (this.ngrok.snapshot().state === "online") this.ngrok.stop();
          else await this.ngrok.start(this.gateway.snapshot().port || await this.gateway.start());
          break;
        case "toggle-gateway":
          if (this.gateway.snapshot().running) await this.stopGateway();
          else await this.startGateway();
          break;
        default:
          throw new Error("Unknown panel action.");
      }
      await this.postState();
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      vscode.window.showErrorMessage(`Devin Remote: ${errorMessage}`);
    }
  }
  /** Sets devin credentials. */
  async setDevinCredentials() {
    const orgId = await vscode.window.showInputBox({
      title: "Devin organization ID",
      prompt: "Organization ID used by the Devin v3 API",
      value: ExtensionController.configuration().orgId,
      placeHolder: "org-…",
      ignoreFocusOut: true
    });
    if (orgId === undefined) return;
    const token = await vscode.window.showInputBox({
      title: "Devin service-user token",
      prompt: "Stored only in VS Code SecretStorage",
      password: true,
      ignoreFocusOut: true
    });
    if (token === undefined) return;
    await vscode.workspace.getConfiguration("devinRemote").update(
      "devin.organizationId",
      orgId.trim(),
      vscode.ConfigurationTarget.Global
    );
    await this.context.secrets.store("devinRemote.devinToken", token.trim());
    this.devin.orgId = orgId.trim();
    this.gateway.refreshAgents();
    await this.postState();
  }
  /**
   * Sets provider credentials.
   * @param {string} preselected Preselected.
   */
  async setProviderCredentials(preselected = "") {
    const provider = preselected || await vscode.window.showQuickPick(
      [
        { label: "OpenAI compatible", id: "openai-compatible" },
        { label: "Anthropic", id: "anthropic" }
      ],
      { title: "LLM provider" }
    ).then(item => item?.id ?? "");
    if (!provider) return;
    const config = vscode.workspace.getConfiguration("devinRemote");
    const modelKey = provider === "anthropic"
      ? "providers.anthropic.model"
      : "providers.openAICompatible.model";
    const model = await vscode.window.showInputBox({
      title: `${provider === "anthropic" ? "Anthropic" : "OpenAI-compatible"} model`,
      value: config.get(modelKey, ""),
      placeHolder: provider === "anthropic" ? "claude-…" : "gpt-…",
      ignoreFocusOut: true
    });
    if (model === undefined) return;
    if (provider === "openai-compatible") {
      const baseUrl = await vscode.window.showInputBox({
        title: "OpenAI-compatible base URL",
        value: config.get("providers.openAICompatible.baseUrl", "https://api.openai.com/v1"),
        ignoreFocusOut: true
      });
      if (baseUrl === undefined) return;
      await config.update(
        "providers.openAICompatible.baseUrl",
        baseUrl.trim(),
        vscode.ConfigurationTarget.Global
      );
      this.providers.configuration.openAIBaseUrl = baseUrl.trim();
    }
    const apiKey = await vscode.window.showInputBox({
      title: "Provider API key",
      prompt: "Stored only in VS Code SecretStorage",
      password: true,
      ignoreFocusOut: true
    });
    if (apiKey === undefined) return;
    await config.update(modelKey, model.trim(), vscode.ConfigurationTarget.Global);
    await this.context.secrets.store(`devinRemote.provider.${provider}`, apiKey.trim());
    if (provider === "anthropic") this.providers.configuration.anthropicModel = model.trim();
    else this.providers.configuration.openAIModel = model.trim();
    await this.postState();
  }
  /** Sets ngrok token. */
  async setNgrokToken() {
    const token = await vscode.window.showInputBox({
      title: "ngrok authtoken",
      prompt: "A free ngrok account provides an authtoken. It is stored in VS Code SecretStorage.",
      password: true,
      ignoreFocusOut: true
    });
    if (token === undefined) return;
    await this.context.secrets.store("devinRemote.ngrokToken", token.trim());
    if (this.gateway.snapshot().running) {
      this.ngrok.stop();
      await new Promise(resolve => { setTimeout(resolve, 250); });
      await this.ngrok.start(this.gateway.snapshot().port);
    }
    await this.postState();
  }
  async dispose() {
    this.ngrok?.dispose();
    await this.gateway?.stop();
  }
}
/** @type {ExtensionController | null} */
let controller = null;
/**
 * Activates the extension.
 * @param {*} context Context.
 */
const activate = context => {
  controller = new ExtensionController(context);
};
/** Deactivates the extension. */
const deactivate = async () => {
  const activeController = controller;
  controller = null;
  await activeController?.dispose();
};
module.exports = { activate, deactivate, ExtensionController };
