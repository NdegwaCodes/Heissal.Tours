"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/client-api";
import type { User } from "@/lib/types";

export function useMe() {
  return useQuery<User>({
    queryKey: ["me"],
    queryFn: () => api.get<User>("auth/me"),
  });
}
