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

/** System / Chinese UI stack — see frontend/DESIGN.md (no webfont dependency). */
const FONT_STACK =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif';

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        locale={zhCN}
        theme={{
          algorithm: theme.defaultAlgorithm,
          token: {
            colorPrimary: "#0071E3",
            colorInfo: "#0071E3",
            colorLink: "#0077ED",
            colorSuccess: "#248A3D",
            colorWarning: "#B86600",
            colorError: "#D70015",
            fontFamily: FONT_STACK,
            borderRadius: 10,
            colorBgBase: "#F5F5F7",
            colorBgLayout: "#F5F5F7",
            colorBgContainer: "#FFFFFF",
            colorBgElevated: "#FFFFFF",
            colorBorder: "rgba(0, 0, 0, 0.08)",
            colorBorderSecondary: "rgba(0, 0, 0, 0.06)",
            colorText: "#1D1D1F",
            colorTextSecondary: "#6E6E73",
            colorTextTertiary: "#86868B",
            colorTextQuaternary: "#AEAEB2",
            colorFillSecondary: "rgba(0, 0, 0, 0.04)",
            colorFillTertiary: "rgba(0, 0, 0, 0.03)",
            colorInfoBg: "rgba(0, 113, 227, 0.09)",
            colorInfoBorder: "rgba(0, 113, 227, 0.22)",
            controlHeight: 36,
            controlOutline: "rgba(0, 113, 227, 0.28)",
          },
          components: {
            Layout: {
              headerBg: "transparent",
              bodyBg: "#F5F5F7",
            },
            Menu: {
              itemBorderRadius: 8,
              itemHeight: 36,
            },
            Table: {
              headerBg: "transparent",
              headerColor: "#6E6E73",
              headerSplitColor: "transparent",
              rowHoverBg: "rgba(0, 0, 0, 0.03)",
              borderColor: "rgba(0, 0, 0, 0.06)",
              colorBgContainer: "transparent",
            },
            Tabs: {
              titleFontSize: 14,
              horizontalItemGutter: 28,
              inkBarColor: "#0071E3",
              itemSelectedColor: "#1D1D1F",
              itemHoverColor: "#0071E3",
              itemColor: "#6E6E73",
            },
            Button: {
              primaryShadow: "none",
              defaultShadow: "none",
              controlHeight: 36,
              controlHeightLG: 40,
              borderRadius: 10,
              defaultBg: "#FFFFFF",
              defaultBorderColor: "rgba(0, 0, 0, 0.12)",
              defaultColor: "#1D1D1F",
            },
            Input: {
              controlHeightLG: 40,
              borderRadiusLG: 10,
              activeBorderColor: "#0071E3",
              hoverBorderColor: "#0071E3",
              colorBgContainer: "#FFFFFF",
            },
            Select: {
              controlHeight: 36,
              colorBgContainer: "#FFFFFF",
              optionSelectedBg: "rgba(0, 113, 227, 0.09)",
              borderRadius: 10,
            },
            Tag: {
              borderRadiusSM: 6,
              defaultBg: "rgba(0, 0, 0, 0.04)",
              defaultColor: "#6E6E73",
            },
            Drawer: {
              colorBgElevated: "#FFFFFF",
            },
            Modal: {
              contentBg: "#FFFFFF",
              headerBg: "#FFFFFF",
              borderRadiusLG: 18,
            },
            Alert: {
              borderRadiusLG: 12,
            },
            Card: {
              colorBgContainer: "#FFFFFF",
              borderRadiusLG: 18,
            },
            Skeleton: {
              gradientFromColor: "rgba(0,0,0,0.04)",
              gradientToColor: "rgba(0,0,0,0.08)",
            },
            Dropdown: {
              borderRadiusLG: 12,
              paddingBlock: 6,
            },
            Segmented: {
              trackBg: "rgba(0, 0, 0, 0.04)",
              itemSelectedBg: "#FFFFFF",
              borderRadius: 10,
              borderRadiusSM: 8,
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
