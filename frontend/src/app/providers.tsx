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
            fontFamily:
              "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans SC', 'Helvetica Neue', Arial, sans-serif",
            borderRadius: 8,
            colorBgLayout: "#f3f5f8",
            colorBorderSecondary: "#e3e8ee",
            colorTextSecondary: "#5b6b7c",
            colorInfoBg: "#eef5fb",
            colorInfoBorder: "#c8ddef",
          },
          components: {
            Layout: {
              siderBg: "#102138",
              headerBg: "#ffffff",
            },
            Menu: {
              darkItemBg: "transparent",
              darkItemSelectedBg: "#1f4e79",
              darkSubMenuItemBg: "#0c1622",
              itemBorderRadius: 8,
              itemMarginInline: 10,
            },
            Table: {
              headerBg: "#f8fafc",
              headerColor: "#5b6b7c",
              headerSplitColor: "transparent",
              rowHoverBg: "#f3f6fa",
            },
            Tabs: {
              titleFontSize: 15,
              horizontalItemGutter: 28,
            },
            Button: {
              controlHeight: 34,
            },
            Tag: {
              borderRadiusSM: 6,
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
