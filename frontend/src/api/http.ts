import axios, { AxiosError } from "axios";

export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "";

export class ApiError extends Error {
  readonly status?: number;
  readonly detail?: unknown;

  constructor(message: string, status?: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

type ErrorBody = {
  message?: string;
  detail?: unknown;
};

export const http = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15000,
});

http.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ErrorBody>) => {
    const body = error.response?.data;
    const message =
      (typeof body?.message === "string" && body.message) ||
      (typeof body?.detail === "string" && body.detail) ||
      (error.code === "ECONNABORTED" ? "请求超时，请稍后重试" : "") ||
      (!error.response ? "无法连接后端服务" : "") ||
      error.message ||
      "请求失败，请稍后重试";
    return Promise.reject(new ApiError(message, error.response?.status, body?.detail));
  },
);
