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
            colorPrimary: "#4F64E8",
            colorInfo: "#4F64E8",
            colorLink: "#4054D3",
            colorSuccess: "#258765",
            colorWarning: "#C47A20",
            colorError: "#C94B53",
            fontFamily: FONT_STACK,
            borderRadius: 12,
            colorBgBase: "#F7F8FA",
            colorBgLayout: "#F7F8FA",
            colorBgContainer: "#FFFFFF",
            colorBgElevated: "#FFFFFF",
            colorBorder: "rgba(20, 24, 31, 0.09)",
            colorBorderSecondary: "rgba(20, 24, 31, 0.06)",
            colorText: "#15171A",
            colorTextSecondary: "#626871",
            colorTextTertiary: "#9298A1",
            colorTextQuaternary: "#B0B5BD",
            colorFillSecondary: "rgba(20, 24, 31, 0.04)",
            colorFillTertiary: "rgba(20, 24, 31, 0.03)",
            colorInfoBg: "#EEF0FF",
            colorInfoBorder: "rgba(79, 100, 232, 0.28)",
            controlHeight: 36,
            controlOutline: "rgba(79, 100, 232, 0.28)",
          },
          components: {
            Layout: {
              headerBg: "transparent",
              bodyBg: "#F7F8FA",
            },
            Menu: {
              itemBorderRadius: 8,
              itemHeight: 36,
            },
            Table: {
              headerBg: "transparent",
              headerColor: "#626871",
              headerSplitColor: "transparent",
              rowHoverBg: "rgba(79, 100, 232, 0.04)",
              borderColor: "rgba(20, 24, 31, 0.06)",
              colorBgContainer: "transparent",
            },
            Tabs: {
              titleFontSize: 14,
              horizontalItemGutter: 24,
              inkBarColor: "#4F64E8",
              itemSelectedColor: "#15171A",
              itemHoverColor: "#4F64E8",
              itemColor: "#626871",
            },
            Button: {
              primaryShadow: "none",
              defaultShadow: "none",
              controlHeight: 36,
              controlHeightLG: 40,
              borderRadius: 10,
              defaultBg: "#FFFFFF",
              defaultBorderColor: "rgba(20, 24, 31, 0.12)",
              defaultColor: "#15171A",
            },
            Input: {
              controlHeightLG: 42,
              borderRadiusLG: 12,
              activeBorderColor: "#4F64E8",
              hoverBorderColor: "#4F64E8",
              colorBgContainer: "#FFFFFF",
            },
            Select: {
              controlHeight: 36,
              colorBgContainer: "#FFFFFF",
              optionSelectedBg: "#EEF0FF",
              borderRadius: 10,
            },
            Tag: {
              borderRadiusSM: 6,
              defaultBg: "rgba(20, 24, 31, 0.04)",
              defaultColor: "#626871",
            },
            Drawer: {
              colorBgElevated: "#FFFFFF",
            },
            Modal: {
              contentBg: "#FFFFFF",
              headerBg: "#FFFFFF",
              borderRadiusLG: 16,
            },
            Alert: {
              borderRadiusLG: 12,
            },
            Card: {
              colorBgContainer: "#FFFFFF",
              borderRadiusLG: 16,
            },
            Skeleton: {
              gradientFromColor: "rgba(20,24,31,0.04)",
              gradientToColor: "rgba(20,24,31,0.09)",
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
