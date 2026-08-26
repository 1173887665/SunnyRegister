import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

const PageActivityContext = createContext(true);

export function CachedPage({ active, children, className }: { active: boolean; children: ReactNode; className?: string }) {
  const parentActive = useContext(PageActivityContext);
  const effectiveActive = parentActive && active;
  return (
    <PageActivityContext.Provider value={effectiveActive}>
      <div className={className} data-page-cache-state={effectiveActive ? "active" : "cached"} hidden={!effectiveActive} aria-hidden={!effectiveActive}>
        {children}
      </div>
    </PageActivityContext.Provider>
  );
}

export function PagePortal({ children }: { children: ReactNode }) {
  const active = useContext(PageActivityContext);
  return createPortal(
    <div data-page-cache-portal style={{ display: active ? "contents" : "none" }} aria-hidden={!active}>
      {children}
    </div>,
    document.body,
  );
}
