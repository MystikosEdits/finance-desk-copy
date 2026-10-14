import { useQuery } from "@tanstack/react-query";
import { get } from "./client";
import type { Account, Category, IncomeSource, Payee, Rule } from "./types";

export const useAccounts = (includeClosed = false) =>
  useQuery({ queryKey: ["accounts", includeClosed], queryFn: () => get<Account[]>(`/api/accounts${includeClosed ? "?include_closed=true" : ""}`) });

export const useCategories = (includeArchived = false) =>
  useQuery({
    queryKey: ["categories", includeArchived],
    queryFn: () => get<Category[]>(`/api/categories${includeArchived ? "?include_archived=true" : ""}`),
    staleTime: 60_000,
  });

export const usePayees = (q = "") => useQuery({ queryKey: ["payees", q], queryFn: () => get<Payee[]>(`/api/payees${q ? `?q=${encodeURIComponent(q)}` : ""}`) });

export const useRules = () => useQuery({ queryKey: ["rules"], queryFn: () => get<Rule[]>("/api/rules") });

export const useIncomeSources = () =>
  useQuery({ queryKey: ["income-sources"], queryFn: () => get<IncomeSource[]>("/api/business/income-sources").catch(() => [] as IncomeSource[]) });
