import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { BrowserRouter } from "react-router-dom";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 15_000,
    },
  },
});

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        locale={zhCN}
        theme={{
          token: {
            colorPrimary: "#1f4e79",
            colorInfo: "#1f4e79",
            colorLink: "#1f4e79",
            fontFamily: "'Source Sans 3', 'Noto Sans SC', sans-serif",
            borderRadius: 6,
            colorBgLayout: "#f4f6f8",
          },
          components: {
            Layout: {
              siderBg: "#102138",
              headerBg: "#ffffff",
            },
            Menu: {
              darkItemBg: "#102138",
              darkItemSelectedBg: "#1f4e79",
              darkSubMenuItemBg: "#0c1622",
            },
          },
        }}
      >
        <AntApp>
          <BrowserRouter>{children}</BrowserRouter>
        </AntApp>
      </ConfigProvider>
    </QueryClientProvider>
  );
}
