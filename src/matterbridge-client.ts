import QRCode from "qrcode";
import WebSocket from "ws";

export interface MatterStatus {
  online: boolean;
  commissioned: boolean;
  advertising: boolean;
  qrPairingCode: string;
  manualPairingCode: string;
  fabrics: Array<{ index: number; name: string }>;
  qrSvg: string;
}

interface MatterResponse {
  online?: boolean;
  commissioned?: boolean;
  advertising?: boolean;
  qrPairingCode?: string;
  manualPairingCode?: string;
  fabricInformations?: Array<{
    fabricIndex?: number;
    rootVendorName?: string;
    label?: string;
  }>;
  matterbridgeInformation?: {
    matterbridgeQrPairingCode?: string;
    matterbridgeManualPairingCode?: string;
    matterbridgePaired?: boolean;
    matterbridgeAdvertise?: boolean;
    matterbridgeFabricInformations?: Array<{
      fabricIndex?: number;
      rootVendorName?: string;
      label?: string;
    }>;
  };
}

export class MatterbridgeClient {
  constructor(private readonly wsUrl: string) {}

  async getStatus(): Promise<MatterStatus> {
    const response = await this.request("/api/settings");
    return this.normalize(response);
  }

  async setCommissioning(open: boolean): Promise<MatterStatus> {
    await this.request(open ? "/api/advertise" : "/api/stopadvertise");
    await new Promise((resolve) => setTimeout(resolve, 150));
    return this.getStatus();
  }

  private async normalize(response: MatterResponse): Promise<MatterStatus> {
    const information = response.matterbridgeInformation;
    const qrPairingCode = information?.matterbridgeQrPairingCode ?? response.qrPairingCode ?? "";
    const fabricInformations = information?.matterbridgeFabricInformations ?? response.fabricInformations ?? [];
    return {
      online: information !== undefined || response.online === true,
      commissioned: information?.matterbridgePaired ?? response.commissioned === true,
      advertising: information?.matterbridgeAdvertise ?? response.advertising === true,
      qrPairingCode,
      manualPairingCode: information?.matterbridgeManualPairingCode ?? response.manualPairingCode ?? "",
      fabrics: fabricInformations.map((fabric) => ({
        index: fabric.fabricIndex ?? 0,
        name: fabric.label || fabric.rootVendorName?.replace(/[()]/g, "") || "Bağlı platform"
      })),
      qrSvg: qrPairingCode
        ? await QRCode.toString(qrPairingCode, { type: "svg", margin: 1, width: 280, color: { dark: "#10233d", light: "#ffffff" } })
        : ""
    };
  }

  private async request(method: string, params: Record<string, unknown> = {}): Promise<MatterResponse> {
    return await new Promise<MatterResponse>((resolve, reject) => {
      const socket = new WebSocket(this.wsUrl);
      const id = Date.now() % 2_000_000_000;
      const timer = setTimeout(() => {
        socket.terminate();
        reject(new Error("Matter bağlantısı zaman aşımına uğradı."));
      }, 5_000);
      const done = (error?: Error, response?: MatterResponse): void => {
        clearTimeout(timer);
        socket.close();
        if (error) reject(error);
        else resolve(response ?? {});
      };
      socket.once("open", () => {
        socket.send(JSON.stringify({
          id,
          sender: "VillaBridge",
          method,
          src: "Frontend",
          dst: "Matterbridge",
          params
        }));
      });
      socket.on("message", (raw) => {
        let message: {
          id?: number;
          method?: string;
          error?: string;
          response?: MatterResponse;
        };
        try {
          message = JSON.parse(raw.toString()) as typeof message;
        } catch {
          return;
        }
        if (message.id !== id || message.method !== method) return;
        if (message.error) done(new Error(message.error));
        else done(undefined, message.response);
      });
      socket.once("error", (error) => done(new Error(`Matter bağlantısı kurulamadı: ${error.message}`)));
    });
  }
}
