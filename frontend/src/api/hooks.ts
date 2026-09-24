import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Params } from "./client";

/** GET with caching; the key is the path plus its params. */
export function useApi<T>(path: string | null, params?: Params, opts: { keepPrevious?: boolean } = {}) {
  return useQuery<T>({
    queryKey: [path, params ?? {}],
    queryFn: () => api<T>(path as string, { params }),
    enabled: path !== null,
    placeholderData: opts.keepPrevious ? keepPreviousData : undefined,
  });
}

/** POST/PUT; on success, refetch everything under the given path prefixes. */
export function useApiMutation<TBody, TResult = unknown>(
  method: "POST" | "PUT",
  path: (body: TBody) => string,
  invalidate: string[] = [],
) {
  const qc = useQueryClient();
  return useMutation<TResult, Error, TBody>({
    mutationFn: (body) => api<TResult>(path(body), { method, body: stripPath(body) }),
    onSuccess: () => {
      for (const prefix of invalidate) {
        qc.invalidateQueries({ predicate: (q) => typeof q.queryKey[0] === "string" && (q.queryKey[0] as string).startsWith(prefix) });
      }
    },
  });
}

// Bodies may carry routing fields prefixed with "_" (e.g. _id); they never go to the API.
function stripPath(body: unknown): unknown {
  if (!body || typeof body !== "object" || Array.isArray(body)) return body;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(body as Record<string, unknown>)) if (!k.startsWith("_")) out[k] = v;
  return Object.keys(out).length ? out : undefined;
}
