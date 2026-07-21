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

const FONT_STACK =
  "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Noto Sans SC', 'Helvetica Neue', Arial, sans-serif";

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        locale={zhCN}
        theme={{
          token: {
            colorPrimary: "#4f46e5",
            colorInfo: "#4f46e5",
            colorLink: "#4f46e5",
            colorSuccess: "#16a34a",
            colorWarning: "#d97706",
            colorError: "#dc2626",
            fontFamily: FONT_STACK,
            borderRadius: 8,
            colorBgLayout: "#f6f5f3",
            colorBgContainer: "#ffffff",
            colorBorder: "#e7e5e4",
            colorBorderSecondary: "#e7e5e4",
            colorText: "#1c1917",
            colorTextSecondary: "#78716c",
            colorTextTertiary: "#a8a29e",
            colorInfoBg: "rgba(79, 70, 229, 0.06)",
            colorInfoBorder: "rgba(79, 70, 229, 0.18)",
            controlHeight: 34,
            controlOutline: "rgba(79, 70, 229, 0.18)",
          },
          components: {
            Layout: {
              siderBg: "#17171c",
              headerBg: "transparent",
              bodyBg: "#f6f5f3",
            },
            Menu: {
              darkItemBg: "transparent",
              darkItemSelectedBg: "rgba(79, 70, 229, 0.28)",
              darkItemHoverBg: "rgba(255, 255, 255, 0.05)",
              darkSubMenuItemBg: "transparent",
              itemBorderRadius: 8,
              itemMarginInline: 8,
              itemHeight: 40,
            },
            Table: {
              headerBg: "#fafaf8",
              headerColor: "#78716c",
              headerSplitColor: "transparent",
              rowHoverBg: "#f6f5f3",
              borderColor: "#e7e5e4",
            },
            Tabs: {
              titleFontSize: 14,
              horizontalItemGutter: 24,
              inkBarColor: "#4f46e5",
              itemSelectedColor: "#4f46e5",
              itemHoverColor: "#4338ca",
              itemColor: "#78716c",
            },
            Button: {
              primaryShadow: "none",
              defaultShadow: "none",
              controlHeight: 34,
              controlHeightLG: 40,
              borderRadius: 8,
            },
            Input: {
              controlHeightLG: 44,
              borderRadiusLG: 10,
              activeBorderColor: "#4f46e5",
              hoverBorderColor: "#a5b4fc",
            },
            Tag: {
              borderRadiusSM: 6,
              defaultBg: "#f6f5f3",
              defaultColor: "#57534e",
            },
            Drawer: {
              colorBgElevated: "#ffffff",
            },
            Alert: {
              borderRadiusLG: 10,
            },
            Select: {
              controlHeight: 32,
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
