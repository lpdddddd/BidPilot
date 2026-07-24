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
  "'Manrope', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif";

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        locale={zhCN}
        theme={{
          algorithm: theme.defaultAlgorithm,
          token: {
            colorPrimary: "#3977F6",
            colorInfo: "#3977F6",
            colorLink: "#2868E8",
            colorSuccess: "#258765",
            colorWarning: "#C47A20",
            colorError: "#C94B53",
            fontFamily: FONT_STACK,
            borderRadius: 12,
            colorBgBase: "#EEF2F7",
            colorBgLayout: "#EEF2F7",
            colorBgContainer: "rgba(255, 255, 255, 0.88)",
            colorBgElevated: "rgba(255, 255, 255, 0.92)",
            colorBorder: "rgba(102, 122, 148, 0.18)",
            colorBorderSecondary: "rgba(102, 122, 148, 0.12)",
            colorText: "#172033",
            colorTextSecondary: "#5F6B7C",
            colorTextTertiary: "#8A96A8",
            colorTextQuaternary: "#A8B3C2",
            colorFillSecondary: "rgba(57, 119, 246, 0.06)",
            colorFillTertiary: "rgba(23, 32, 51, 0.03)",
            colorInfoBg: "rgba(57, 119, 246, 0.12)",
            colorInfoBorder: "rgba(57, 119, 246, 0.28)",
            controlHeight: 36,
            controlOutline: "rgba(57, 119, 246, 0.28)",
          },
          components: {
            Layout: {
              headerBg: "transparent",
              bodyBg: "#EEF2F7",
            },
            Menu: {
              itemBorderRadius: 8,
              itemHeight: 36,
            },
            Table: {
              headerBg: "transparent",
              headerColor: "#5F6B7C",
              headerSplitColor: "transparent",
              rowHoverBg: "rgba(57, 119, 246, 0.05)",
              borderColor: "rgba(102, 122, 148, 0.12)",
              colorBgContainer: "transparent",
            },
            Tabs: {
              titleFontSize: 14,
              horizontalItemGutter: 24,
              inkBarColor: "#3977F6",
              itemSelectedColor: "#172033",
              itemHoverColor: "#3977F6",
              itemColor: "#5F6B7C",
            },
            Button: {
              primaryShadow: "none",
              defaultShadow: "none",
              controlHeight: 36,
              controlHeightLG: 40,
              borderRadius: 10,
              defaultBg: "rgba(255, 255, 255, 0.72)",
              defaultBorderColor: "rgba(102, 122, 148, 0.22)",
              defaultColor: "#172033",
            },
            Input: {
              controlHeightLG: 42,
              borderRadiusLG: 12,
              activeBorderColor: "#3977F6",
              hoverBorderColor: "#3977F6",
              colorBgContainer: "rgba(255, 255, 255, 0.82)",
            },
            Select: {
              controlHeight: 36,
              colorBgContainer: "rgba(255, 255, 255, 0.82)",
              optionSelectedBg: "rgba(57, 119, 246, 0.12)",
              borderRadius: 10,
            },
            Tag: {
              borderRadiusSM: 6,
              defaultBg: "rgba(255, 255, 255, 0.55)",
              defaultColor: "#5F6B7C",
            },
            Drawer: {
              colorBgElevated: "rgba(255, 255, 255, 0.94)",
            },
            Modal: {
              contentBg: "rgba(255, 255, 255, 0.94)",
              headerBg: "transparent",
              borderRadiusLG: 16,
            },
            Alert: {
              borderRadiusLG: 12,
            },
            Card: {
              colorBgContainer: "rgba(255, 255, 255, 0.72)",
              borderRadiusLG: 16,
            },
            Skeleton: {
              gradientFromColor: "rgba(23,32,51,0.04)",
              gradientToColor: "rgba(23,32,51,0.09)",
            },
            Dropdown: {
              borderRadiusLG: 12,
              paddingBlock: 6,
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
