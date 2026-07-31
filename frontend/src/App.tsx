import { AuthBoundary } from "./auth/AuthBoundary";
import { Workbench } from "./workbench/Workbench";

export function App() {
  return (
    <AuthBoundary>
      {(session, performLogout, logoutPending, expireSession) => (
        <Workbench
          session={session}
          performLogout={performLogout}
          logoutPending={logoutPending}
          onSessionExpired={expireSession}
        />
      )}
    </AuthBoundary>
  );
}
