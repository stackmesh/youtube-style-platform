export type CheckStatus = "ok" | "fail";

export type ReadyResponse = {
  status: "ready" | "not_ready";
  checks: {
    postgres: CheckStatus;
    redis: CheckStatus;
    s3: CheckStatus;
    worker: CheckStatus;
  };
};

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export async function getReadiness(): Promise<ReadyResponse> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}/health/ready`, {
      headers: { Accept: "application/json" },
    });
  } catch {
    throw new Error("Could not reach the API.");
  }
  if (!response.ok && response.status !== 503) {
    throw new Error("Could not reach the API.");
  }
  return (await response.json()) as ReadyResponse;
}
