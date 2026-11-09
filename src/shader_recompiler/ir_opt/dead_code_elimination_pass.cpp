// SPDX-FileCopyrightText: Copyright 2021 yuzu Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include "shader_recompiler/frontend/ir/basic_block.h"
#include "shader_recompiler/frontend/ir/value.h"
#include "shader_recompiler/ir_opt/passes.h"

namespace Shader::Optimization {

void DeadCodeEliminationPass(IR::Program& program) {
    // We iterate over the instructions in reverse order.
    // This is because removing an instruction reduces the number of uses for earlier instructions.
    for (IR::Block* const block : program.post_order_blocks) {
        auto it{block->end()};
        while (it != block->begin()) {
            --it;
            if (!it->HasUses() && !it->MayHaveSideEffects()) {
                it->Invalidate();
                it = block->Instructions().erase(it);
            }
        }
    }
}

void RemoveDeadIdentitiesPass(IR::Program& program) {
    // Only the husks the SSA rewrite and constant propagation leave behind: unused identities
    // and invalidated instructions. Most of a lowered program is such husks until the dead code
    // elimination at the end, and every pass before it walked them. Other unused instructions
    // stay: a dead global load or texture read still contributes its descriptor.
    for (IR::Block* const block : program.post_order_blocks) {
        auto it{block->end()};
        while (it != block->begin()) {
            --it;
            const IR::Opcode opcode{it->GetOpcode()};
            if ((opcode == IR::Opcode::Identity || opcode == IR::Opcode::Void) && !it->HasUses()) {
                it->Invalidate();
                it = block->Instructions().erase(it);
            }
        }
    }
}

} // namespace Shader::Optimization
