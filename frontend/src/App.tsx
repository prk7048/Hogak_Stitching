import { Navigate, Route, Routes } from "react-router-dom";

import { Shell } from "./components/Shell";
import { ArtifactsPage } from "./pages/ArtifactsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { GeometryComparePage } from "./pages/GeometryComparePage";
import { OutputsPage } from "./pages/OutputsPage";
import { ValidationPage } from "./pages/ValidationPage";

export function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Navigate to="/bakeoff" replace />} />
        <Route path="/dashboard" element={<Navigate to="/run" replace />} />
        <Route path="/geometry-compare" element={<Navigate to="/bakeoff" replace />} />
        <Route path="/validation" element={<Navigate to="/validate" replace />} />
        <Route path="/calibration/start" element={<Navigate to="/bakeoff" replace />} />
        <Route path="/calibration/assisted" element={<Navigate to="/bakeoff" replace />} />
        <Route path="/calibration/review" element={<Navigate to="/bakeoff" replace />} />
        <Route path="/calibration/stitch-review" element={<Navigate to="/bakeoff" replace />} />
        <Route path="/bakeoff" element={<GeometryComparePage />} />
        <Route path="/run" element={<DashboardPage />} />
        <Route path="/validate" element={<ValidationPage />} />
        <Route path="/outputs" element={<OutputsPage />} />
        <Route path="/artifacts" element={<ArtifactsPage />} />
      </Routes>
    </Shell>
  );
}
