import { apiRequest } from "./client";

export const MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024;
export const PART_BATCH = 20;

const ALLOWED_TYPES = new Set([
  "video/mp4",
  "video/quicktime",
  "video/webm",
  "video/x-matroska",
  "video/mpeg",
]);

const ALLOWED_EXTENSIONS = [".mp4", ".mov", ".webm", ".mkv", ".mpeg", ".mpg"];

export type UploadSession = {
  session_id: string;
  status: string;
  part_size: number;
  part_count: number;
  expected_size: number;
  expires_at: string;
  completed_at?: string | null;
  error_code?: string | null;
};

export type UploadPart = {
  part_number: number;
  url: string;
  content_length: number;
  expires_at: string;
};

export type ProcessingStatus = {
  processing_status: string;
  attempt: number | null;
  error_code: string | null;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  auto_thumbnail_ready: boolean;
};

export function validateVideoFile(file: Pick<File, "name" | "size" | "type">): string | null {
  if (file.size < 1) {
    return "Choose a video file.";
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return "The file is larger than 2 GB.";
  }
  const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
  if (!ALLOWED_TYPES.has(file.type) && !ALLOWED_EXTENSIONS.includes(extension)) {
    return "Use an MP4, MOV, WebM, MKV, or MPEG file.";
  }
  return null;
}

export function createUpload(videoId: string, byteSize: number, contentType: string): Promise<UploadSession> {
  return apiRequest<UploadSession>(
    `/api/v1/videos/${videoId}/upload`,
    { method: "POST", body: JSON.stringify({ byte_size: byteSize, content_type: contentType }) },
    true,
  );
}

export function signParts(videoId: string, partNumbers: number[]): Promise<{ parts: UploadPart[] }> {
  return apiRequest<{ parts: UploadPart[] }>(
    `/api/v1/videos/${videoId}/upload/parts`,
    { method: "POST", body: JSON.stringify({ part_numbers: partNumbers }) },
    true,
  );
}

export function completeUpload(
  videoId: string,
  parts: { part_number: number; etag: string }[],
): Promise<unknown> {
  return apiRequest(
    `/api/v1/videos/${videoId}/upload/complete`,
    { method: "POST", body: JSON.stringify({ parts }) },
    true,
  );
}

export function abortUpload(videoId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/videos/${videoId}/upload/abort`, { method: "POST" }, true);
}

export function getProcessing(videoId: string): Promise<ProcessingStatus> {
  return apiRequest<ProcessingStatus>(`/api/v1/videos/${videoId}/processing`, {}, true);
}

export function retryProcessing(videoId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/videos/${videoId}/processing/retry`, { method: "POST" }, true);
}

export type PutPart = (
  url: string,
  body: Blob,
  onProgress: (loaded: number) => void,
  signal: AbortSignal,
) => Promise<string>;

export async function uploadVideoFile(
  videoId: string,
  file: File,
  options: { onProgress: (fraction: number) => void; signal: AbortSignal; putPart?: PutPart },
): Promise<void> {
  const contentType = ALLOWED_TYPES.has(file.type) ? file.type : "video/mp4";
  const session = await createUpload(videoId, file.size, contentType);
  const put = options.putPart ?? putPartWithXhr;
  const completed: { part_number: number; etag: string }[] = [];
  let sent = 0;
  for (let start = 1; start <= session.part_count; start += PART_BATCH) {
    const numbers = Array.from(
      { length: Math.min(PART_BATCH, session.part_count - start + 1) },
      (_, index) => start + index,
    );
    const signed = await signParts(videoId, numbers);
    for (const part of signed.parts) {
      if (options.signal.aborted) {
        await abortUpload(videoId);
        throw new Error("Upload cancelled.");
      }
      const blob = file.slice(sent, sent + part.content_length);
      if (blob.size !== part.content_length) {
        throw new Error("The file changed before the upload finished.");
      }
      const etag = await putWithRetry(videoId, part, blob, (loaded) => {
        options.onProgress((sent + loaded) / file.size);
      }, options.signal, put);
      sent += part.content_length;
      completed.push({ part_number: part.part_number, etag });
      options.onProgress(sent / file.size);
    }
  }
  await completeUpload(videoId, completed);
}

async function putWithRetry(
  videoId: string,
  part: UploadPart,
  blob: Blob,
  onProgress: (loaded: number) => void,
  signal: AbortSignal,
  put: PutPart,
): Promise<string> {
  let current = part;
  let lastError: unknown;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      return await put(current.url, blob, onProgress, signal);
    } catch (caught) {
      lastError = caught;
      if (signal.aborted) {
        await abortUpload(videoId);
        throw new Error("Upload cancelled.");
      }
      const refreshed = await signParts(videoId, [part.part_number]);
      const next = refreshed.parts[0];
      if (!next) {
        break;
      }
      current = next;
    }
  }
  throw lastError instanceof Error ? lastError : new Error("The upload part failed.");
}

export function putPartWithXhr(
  url: string,
  body: Blob,
  onProgress: (loaded: number) => void,
  signal: AbortSignal,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const stop = () => xhr.abort();
    signal.addEventListener("abort", stop, { once: true });
    xhr.open("PUT", url);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(event.loaded);
      }
    };
    xhr.onload = () => {
      signal.removeEventListener("abort", stop);
      if (xhr.status >= 200 && xhr.status < 300) {
        const etag = xhr.getResponseHeader("ETag");
        if (!etag) {
          reject(new Error("The storage service did not confirm the part."));
          return;
        }
        resolve(etag);
        return;
      }
      reject(new Error("The storage service rejected the part."));
    };
    xhr.onerror = () => {
      signal.removeEventListener("abort", stop);
      reject(new Error("The storage service could not be reached."));
    };
    xhr.onabort = () => {
      signal.removeEventListener("abort", stop);
      reject(new Error("Upload cancelled."));
    };
    xhr.send(body);
  });
}
