import { NavLink, useLocation } from "react-router-dom";

import { routesForGroup } from "../lib/routes";

export function CalibrationStepper() {
  const location = useLocation();
  const steps = routesForGroup("calibration");
  const activeIndex = Math.max(
    0,
    steps.findIndex((step) => step.path === location.pathname),
  );

  return (
    <nav className="stepper" aria-label="캘리브레이션 단계">
      {steps.map((step, index) => {
        const isComplete = index < activeIndex;
        return (
          <NavLink
            key={step.path}
            to={step.path}
            className={({ isActive }) =>
              `stepper-link${isActive ? " active" : ""}${isComplete ? " complete" : ""}`
            }
          >
            <span className="stepper-index">{index + 1}</span>
            <span className="stepper-copy">
              <span className="stepper-label">{step.label}</span>
              <span className="stepper-summary">{step.summary}</span>
            </span>
          </NavLink>
        );
      })}
    </nav>
  );
}
