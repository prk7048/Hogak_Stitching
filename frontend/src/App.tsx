import { Navigate, Route, Routes } from "react-router-dom";

import { Shell } from "./components/Shell";
import { DashboardPage } from "./pages/DashboardPage";
import { ValidationPage } from "./pages/ValidationPage";

export function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Navigate to="/run" replace />} />
        <Route path="/dashboard" element={<Navigate to="/run" replace />} />
        <Route path="/validation" element={<Navigate to="/validate" replace />} />
        <Route path="/outputs" element={<Navigate to="/run" replace />} />
        <Route path="/artifacts" element={<Navigate to="/validate" replace />} />
        <Route path="/geometry-compare" element={<Navigate to="/run" replace />} />
        <Route path="/bakeoff" element={<Navigate to="/run" replace />} />
        <Route path="/calibration/*" element={<Navigate to="/run" replace />} />
        <Route path="/run" element={<DashboardPage />} />
        <Route path="/validate" element={<ValidationPage />} />
        <Route path="*" element={<Navigate to="/run" replace />} />
      </Routes>
    </Shell>
  );
}
