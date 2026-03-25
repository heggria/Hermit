// Project Policy tab -- renders the same global policy view.
// Policy is global (not project-scoped), so we reuse the Policy page directly.

import Policy from '@/pages/Policy';

export default function ProjectPolicy() {
  return (
    <div className="p-4 sm:p-6">
      <Policy />
    </div>
  );
}
