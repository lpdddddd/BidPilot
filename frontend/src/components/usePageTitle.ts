import { useEffect } from "react";

export function usePageTitle(title: string) {
  useEffect(() => {
    document.title = title ? `${title} · BidPilot` : "BidPilot";
    return () => {
      document.title = "BidPilot";
    };
  }, [title]);
}
