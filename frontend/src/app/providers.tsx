import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp, ConfigProvider, theme } from "antd";
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
          algorithm: theme.darkAlgorithm,
          token: {
            colorPrimary: "#7c5cff",
            colorInfo: "#7c5cff",
            colorLink: "#a78bfa",
            colorSuccess: "#3dba7a",
            colorWarning: "#d4a017",
            colorError: "#e25555",
            fontFamily: FONT_STACK,
            borderRadius: 12,
            colorBgBase: "#0a0a0f",
            colorBgLayout: "#0a0a0f",
            colorBgContainer: "#12121a",
            colorBgElevated: "#181821",
            colorBorder: "rgba(255, 255, 255, 0.08)",
            colorBorderSecondary: "rgba(255, 255, 255, 0.06)",
            colorText: "#ececf1",
            colorTextSecondary: "#9b9bb0",
            colorTextTertiary: "#6b6b80",
            colorTextQuaternary: "#525266",
            colorFillSecondary: "rgba(255, 255, 255, 0.04)",
            colorFillTertiary: "rgba(255, 255, 255, 0.03)",
            colorInfoBg: "rgba(124, 92, 255, 0.12)",
            colorInfoBorder: "rgba(124, 92, 255, 0.28)",
            controlHeight: 34,
            controlOutline: "rgba(124, 92, 255, 0.28)",
          },
          components: {
            Layout: {
              siderBg: "#0c0c12",
              headerBg: "transparent",
              bodyBg: "#0a0a0f",
              triggerBg: "#12121a",
            },
            Menu: {
              darkItemBg: "transparent",
              darkItemSelectedBg: "rgba(124, 92, 255, 0.14)",
              darkItemHoverBg: "#181821",
              darkSubMenuItemBg: "transparent",
              itemBorderRadius: 10,
              itemMarginInline: 8,
              itemHeight: 40,
            },
            Table: {
              headerBg: "#181821",
              headerColor: "#9b9bb0",
              headerSplitColor: "transparent",
              rowHoverBg: "#20202b",
              borderColor: "rgba(255, 255, 255, 0.07)",
              colorBgContainer: "#12121a",
            },
            Tabs: {
              titleFontSize: 14,
              horizontalItemGutter: 28,
              inkBarColor: "#7c5cff",
              itemSelectedColor: "#ececf1",
              itemHoverColor: "#c4b5fd",
              itemColor: "#9b9bb0",
            },
            Button: {
              primaryShadow: "none",
              defaultShadow: "none",
              controlHeight: 34,
              controlHeightLG: 40,
              borderRadius: 10,
              defaultBg: "#181821",
              defaultBorderColor: "rgba(255, 255, 255, 0.1)",
              defaultColor: "#ececf1",
            },
            Input: {
              controlHeightLG: 44,
              borderRadiusLG: 12,
              activeBorderColor: "#7c5cff",
              hoverBorderColor: "rgba(124, 92, 255, 0.55)",
              colorBgContainer: "#12121a",
            },
            Select: {
              controlHeight: 32,
              colorBgContainer: "#12121a",
              optionSelectedBg: "rgba(124, 92, 255, 0.16)",
            },
            Tag: {
              borderRadiusSM: 6,
              defaultBg: "rgba(255, 255, 255, 0.05)",
              defaultColor: "#9b9bb0",
            },
            Drawer: {
              colorBgElevated: "#12121a",
            },
            Modal: {
              contentBg: "#12121a",
              headerBg: "#12121a",
            },
            Alert: {
              borderRadiusLG: 12,
            },
            Card: {
              colorBgContainer: "#12121a",
            },
            Skeleton: {
              gradientFromColor: "rgba(255,255,255,0.06)",
              gradientToColor: "rgba(255,255,255,0.12)",
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
