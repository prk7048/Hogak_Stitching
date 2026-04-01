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
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/calibration/start" element={<Navigate to="/geometry-compare" replace />} />
        <Route path="/calibration/assisted" element={<Navigate to="/geometry-compare" replace />} />
        <Route path="/calibration/review" element={<Navigate to="/geometry-compare" replace />} />
        <Route path="/calibration/stitch-review" element={<Navigate to="/geometry-compare" replace />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/validation" element={<ValidationPage />} />
        <Route path="/geometry-compare" element={<GeometryComparePage />} />
        <Route path="/outputs" element={<OutputsPage />} />
        <Route path="/artifacts" element={<ArtifactsPage />} />
      </Routes>
    </Shell>
  );
}
