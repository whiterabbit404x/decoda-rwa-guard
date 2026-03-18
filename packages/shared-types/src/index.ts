export type HealthStatus = {
  status: "ok" | "degraded" | "down";
  service: string;
};

export type TreasuryPosition = {
  instrumentId: string;
  notionalUsd: number;
  durationDays: number;
  updatedAt: string;
};
