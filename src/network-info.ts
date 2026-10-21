import { isIP } from "node:net";
import { networkInterfaces } from "node:os";

export interface NetworkInterfaceAddress {
  address: string;
  family: string | number;
  internal: boolean;
}

export interface NetworkInfo {
  addresses: string[];
  preferredAddress: string | null;
}

type NetworkInterfaceMap = NodeJS.Dict<readonly NetworkInterfaceAddress[]>;

interface Candidate {
  address: string;
  interfaceName: string;
}

function interfaceRank(name: string): number {
  if (/^(wlan|wifi|wl)/i.test(name)) return 0;
  if (/^(eth|en\d)/i.test(name)) return 1;
  return 2;
}

function addressRank(address: string): number {
  const parts = address.split(".").map(Number);
  if (parts[0] === 192 && parts[1] === 168) return 0;
  if (parts[0] === 10) return 0;
  if (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) return 0;
  if (parts[0] === 169 && parts[1] === 254) return 2;
  return 1;
}

function numericAddress(address: string): number {
  return address.split(".").reduce((result, part) => result * 256 + Number(part), 0);
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

function isUsableAddress(address: string): boolean {
  const first = Number(address.split(".")[0]);
  return first !== 0 && first !== 127 && address !== "255.255.255.255";
}

export function networkInfoFromInterfaces(interfaces: NetworkInterfaceMap): NetworkInfo {
  const candidates: Candidate[] = [];
  for (const interfaceName of Object.keys(interfaces).sort()) {
    for (const entry of interfaces[interfaceName] ?? []) {
      if (
        entry.internal ||
        (entry.family !== "IPv4" && entry.family !== 4) ||
        isIP(entry.address) !== 4 ||
        !isUsableAddress(entry.address)
      ) {
        continue;
      }
      candidates.push({ address: entry.address, interfaceName });
    }
  }

  candidates.sort((left, right) =>
    addressRank(left.address) - addressRank(right.address) ||
    interfaceRank(left.interfaceName) - interfaceRank(right.interfaceName) ||
    compareText(left.interfaceName, right.interfaceName) ||
    numericAddress(left.address) - numericAddress(right.address)
  );

  const addresses = [...new Set(candidates.map((candidate) => candidate.address))];
  return {
    addresses,
    preferredAddress: addresses[0] ?? null
  };
}

export function getNetworkInfo(): NetworkInfo {
  return networkInfoFromInterfaces(networkInterfaces());
}
